"""
stage_controller.py
===================
CascadeNucleusController — communicates with a Cascade / Nucleus prober
over three transports: Simulation, VISA, or TCP Socket.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import List, Optional, Tuple

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False

from .constants import (
    CASCADE_DEFAULT_DEVICE_ID,
    CASCADE_KNOWN_DEVICE_IDS,
    CASCADE_DEVICE_ID_COMMAND_PREFIXES,
)


class CascadeNucleusController:
    """Generic controller for Cascade / Nucleus remote interface.

    Transports
    ----------
    Simulation  — no hardware required; all commands return a ``SIMULATED:``
                  echo after a short artificial delay.
    VISA        — opens a pyvisa session (GPIB, RS-232, TCPIP INSTR, …).
    TCP Socket  — raw TCP connection to a Nucleus TCP server (default port
                  8765 on 127.0.0.1 for the Cascade Summit AP controller).

    Thread safety
    -------------
    All I/O is protected by ``self.lock``.  ``connect()`` and
    ``disconnect()`` do **not** acquire the lock so that ``send_command()``
    can call them safely from inside a ``with self.lock:`` block during the
    reconnect-retry path.
    """

    def __init__(self) -> None:
        self.rm = None
        self.inst = None
        self.sock: Optional[socket.socket] = None
        self.connected: bool = False
        self.transport: str = "Simulation"
        self.address: str = ""
        self.idn: str = ""
        self.lock = threading.Lock()

    # ------------------------------------------------------------------
    # VISA helper
    # ------------------------------------------------------------------

    def _resource_manager(self):
        try:
            return pyvisa.ResourceManager()
        except Exception:
            return pyvisa.ResourceManager("@py")

    def list_resources(self) -> List[str]:
        """Return all VISA resources visible from any available backend."""
        if not PYVISA_AVAILABLE:
            return []
        resources: List[str] = []
        seen: set = set()
        for backend in (None, "@py"):
            rm = None
            try:
                rm = (
                    pyvisa.ResourceManager()
                    if backend is None
                    else pyvisa.ResourceManager(backend)
                )
                for resource in rm.list_resources():
                    if resource not in seen:
                        seen.add(resource)
                        resources.append(resource)
            except BaseException:
                continue
            finally:
                try:
                    if rm is not None:
                        rm.close()
                except Exception:
                    pass
        return sorted(resources)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _parse_host_port(self, address: str) -> Tuple[str, int]:
        host, sep, port = address.strip().partition(":")
        if not sep:
            raise ValueError("TCP address must be host:port, e.g. 127.0.0.1:8765")
        return host.strip(), int(port)

    def connect(
        self, transport: str, address: str, timeout_ms: int = 5000
    ) -> Tuple[bool, str]:
        """Open a connection to the Cascade stage.

        Returns ``(success, message)``.
        """
        self.disconnect()
        self.transport = transport
        self.address = address.strip()
        try:
            if transport == "Simulation":
                self.connected = True
                self.idn = "Cascade Stage Simulator"
                return True, self.idn

            if transport == "VISA":
                if not PYVISA_AVAILABLE:
                    return False, "pyvisa not installed"
                if not self.address:
                    return False, "No VISA resource selected"
                # Retry once: NI-VISA may need a moment on first use.
                last_exc: Optional[Exception] = None
                for attempt in range(2):
                    if attempt > 0:
                        time.sleep(1.5)
                    try:
                        self.rm = self._resource_manager()
                        self.inst = self.rm.open_resource(self.address)
                        self.inst.timeout = timeout_ms
                        try:
                            self.inst.write_termination = "\n"
                            self.inst.read_termination = "\n"
                        except Exception:
                            pass
                        try:
                            self.idn = self.inst.query("*IDN?").strip()
                        except Exception:
                            self.idn = f"Connected to {self.address}"
                        self.connected = True
                        return True, self.idn
                    except Exception as exc:
                        last_exc = exc
                        try:
                            if self.inst is not None:
                                self.inst.close()
                        except Exception:
                            pass
                        try:
                            if self.rm is not None:
                                self.rm.close()
                        except Exception:
                            pass
                        self.inst = None
                        self.rm = None
                raise last_exc  # type: ignore[misc]

            if transport == "TCP Socket":
                host, port = self._parse_host_port(self.address)
                self.sock = socket.create_connection(
                    (host, port), timeout_ms / 1000.0
                )
                self.sock.settimeout(timeout_ms / 1000.0)
                self.connected = True
                self.idn = f"TCP stage at {host}:{port}"
                return True, self.idn

            return False, f"Unsupported transport: {transport}"

        except Exception as exc:
            self.disconnect()
            return False, f"Stage connection failed: {exc}"

    def disconnect(self) -> None:
        """Close all open handles and reset state."""
        if self.inst is not None:
            try:
                self.inst.close()
            except Exception:
                pass
        if self.rm is not None:
            try:
                self.rm.close()
            except Exception:
                pass
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
        self.inst = None
        self.rm = None
        self.sock = None
        self.connected = False
        self.idn = ""

    # ------------------------------------------------------------------
    # Command helpers
    # ------------------------------------------------------------------

    def _split_commands(self, command: str) -> List[str]:
        return [part.strip() for part in command.split(";") if part.strip()]

    def _normalize_command(self, command: str) -> str:
        """Inject the default device-ID into commands that require one."""
        parts = command.split()
        if not parts:
            return command
        head = parts[0]
        if not head.lower().startswith(CASCADE_DEVICE_ID_COMMAND_PREFIXES):
            return command
        if len(parts) > 1 and parts[1] in CASCADE_KNOWN_DEVICE_IDS:
            return command
        return " ".join([head, CASCADE_DEFAULT_DEVICE_ID, *parts[1:]])

    def preview_commands(self, command: str) -> List[str]:
        """Return the normalized form of each sub-command without sending."""
        return [
            self._normalize_command(item)
            for item in self._split_commands(command)
        ]

    # ------------------------------------------------------------------
    # TCP socket I/O
    # ------------------------------------------------------------------

    def _read_socket_reply(self) -> str:
        if self.sock is None:
            return ""
        chunks = []
        while True:
            data = self.sock.recv(4096)
            if not data:
                break
            chunks.append(data)
            if b"\n" in data or len(data) < 4096:
                break
        return b"".join(chunks).decode(errors="ignore").strip()

    def _try_read_socket_reply(self, timeout_s: float = 0.3) -> str:
        if self.sock is None:
            return ""
        previous_timeout = self.sock.gettimeout()
        try:
            self.sock.settimeout(timeout_s)
            return self._read_socket_reply()
        except socket.timeout:
            return ""
        finally:
            self.sock.settimeout(previous_timeout)

    # ------------------------------------------------------------------
    # Core command dispatch
    # ------------------------------------------------------------------

    def send_command(
        self, command: str, expect_reply: Optional[bool] = None
    ) -> str:
        """Send one or more semicolon-separated commands and return the reply.

        On I/O failure, reconnects once silently and retries — handles stale
        VISA sessions and dropped TCP connections that occur while the stage
        is idle during a long measurement.
        """
        command = command.strip()
        if not command:
            return "Skipped empty command"
        commands = [
            self._normalize_command(item)
            for item in self._split_commands(command)
        ]
        if not commands:
            return "Skipped empty command"

        with self.lock:
            if not self.connected:
                raise RuntimeError("Stage not connected")

            if self.transport == "Simulation":
                time.sleep(0.15)
                return " | ".join(f"SIMULATED: {item}" for item in commands)

            for _attempt in range(2):
                try:
                    if self.transport == "VISA" and self.inst is not None:
                        replies: List[str] = []
                        for item in commands:
                            item_expect_reply = (
                                item.endswith("?")
                                if expect_reply is None
                                else expect_reply
                            )
                            if item_expect_reply:
                                replies.append(self.inst.query(item).strip())
                            else:
                                self.inst.write(item)
                        return (
                            " | ".join(r for r in replies if r) or "OK"
                        )

                    if self.transport == "TCP Socket" and self.sock is not None:
                        replies = []
                        for item in commands:
                            item_expect_reply = (
                                item.endswith("?")
                                if expect_reply is None
                                else expect_reply
                            )
                            payload = (item + "\r\n").encode(
                                "ascii", errors="ignore"
                            )
                            self.sock.sendall(payload)
                            reply = (
                                self._read_socket_reply()
                                if item_expect_reply
                                else self._try_read_socket_reply()
                            )
                            if reply:
                                replies.append(reply)
                        return (
                            " | ".join(r for r in replies if r)
                            or "Command sent (no explicit reply)"
                        )

                    break  # No valid handle — do not retry

                except Exception:
                    if _attempt == 0:
                        ok, _msg = self.connect(self.transport, self.address)
                        if not ok:
                            raise
                    else:
                        raise

        raise RuntimeError("Unknown stage transport state")

    # ------------------------------------------------------------------
    # High-level motion commands
    # ------------------------------------------------------------------

    def lift(self, command: str) -> str:
        """Separate probe tips (chuck moves down)."""
        return self.send_command(command, expect_reply=False)

    def lower(self, command: str) -> str:
        """Contact probe tips (chuck moves up)."""
        return self.send_command(command, expect_reply=False)

    def home(self, command: str) -> str:
        """Move to first die / reference position."""
        return self.send_command(command, expect_reply=False)

    def move_next(
        self,
        dx_um: float,
        dy_um: float,
        use_relative_move: bool,
        move_template: str,
        next_site_command: str,
        site: int,
    ) -> str:
        """Step to the next wafer site, either by relative XY move or by
        wafer-map command."""
        if not use_relative_move and next_site_command.strip():
            cmd = next_site_command.format(site=site)
            return self.send_command(cmd, expect_reply=False)
        cmd = move_template.format(
            dx_um=dx_um,
            dy_um=dy_um,
            dx_mm=dx_um / 1000.0,
            dy_mm=dy_um / 1000.0,
            site=site,
        )
        return self.send_command(cmd, expect_reply=False)
