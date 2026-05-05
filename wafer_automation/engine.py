"""
engine.py
=========
WaferAutomationEngine — orchestrates the full multi-site measurement loop:

    lower tips → measure site 1
    → lift → move → lower → measure site 2
    → … → measure site N → lift tips

GUI-agnostic: communicates exclusively through callback hooks so it can be
driven by a PyQt5 worker thread, a CLI script, or a test.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from b1500_powermeter_rollover import (
    B1500Controller,
    MeasurementPoint,
    RolloverResult,
    SweepConfig,
    SynchronizedMeasurementEngine,
    ThorlabsPowerMeterController,
)

from .constants import SCAN_MODE_COL_TTB
from .models import AutomationConfig, SiteSummary, SpectrometerHookConfig, StageConfig
from .stage_controller import CascadeNucleusController


class WaferAutomationEngine:
    """Drives the per-site measurement loop with rollover detection.

    Callback hooks (all optional)
    ------------------------------
    on_log(str)                          — timestamped log line
    on_progress(current_int, total_int)  — after each site completes
    on_point_complete(payload)           — per measurement point; payload is
                                           ``(site_number, MeasurementPoint)``
    on_site_complete(SiteSummary)        — after each site
    on_rollover_detected(int, RolloverResult) — ``(site_number, result)``
    """

    def __init__(
        self,
        b1500: B1500Controller,
        power_meter: ThorlabsPowerMeterController,
        stage: CascadeNucleusController,
        sweep_config: SweepConfig,
        stage_config: StageConfig,
        automation_config: AutomationConfig,
        spectrometer_hook: SpectrometerHookConfig,
    ) -> None:
        self.b1500 = b1500
        self.power_meter = power_meter
        self.stage = stage
        self.sweep_config = sweep_config
        self.stage_config = stage_config
        self.automation_config = automation_config
        self.spectrometer_hook = spectrometer_hook

        self.running: bool = False
        self.stop_requested: bool = False
        self.site_summaries: List[SiteSummary] = []

        self.on_log: Optional[Callable[[str], None]] = None
        self.on_progress: Optional[Callable[[int, int], None]] = None
        self.on_point_complete: Optional[Callable] = None
        self.on_site_complete: Optional[Callable[[SiteSummary], None]] = None
        self.on_rollover_detected: Optional[Callable] = None

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        msg = f"[{ts}] {message}"
        print(msg)
        if self.on_log:
            self.on_log(msg)

    # ------------------------------------------------------------------
    # Stop control
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self.stop_requested = True

    # ------------------------------------------------------------------
    # Timing / settling
    # ------------------------------------------------------------------

    def _wait_with_stop(self, duration_s: float) -> bool:
        """Sleep for *duration_s* seconds, checking for stop every 50 ms.

        Returns ``True`` if the full duration elapsed, ``False`` if stopped.
        """
        deadline = time.time() + max(0.0, duration_s)
        while time.time() < deadline:
            if self.stop_requested:
                self.log("Stop requested during stage settling")
                return False
            time.sleep(min(0.05, max(0.0, deadline - time.time())))
        return True

    # ------------------------------------------------------------------
    # Wafer-map helpers
    # ------------------------------------------------------------------

    def _sequence_index(self, run_index: int) -> int:
        """Map loop index → grid sequence index.

        The current physical stage position is always treated as grid origin
        (row 1, col 1).  ``start_site`` only affects output file naming.
        """
        return run_index

    def _site_row_col(self, sequence_index: int) -> Tuple[int, int]:
        rows = max(1, self.automation_config.map_rows)
        cols = max(1, self.automation_config.map_cols)
        if self.automation_config.scan_mode == SCAN_MODE_COL_TTB:
            column_index, row_index = divmod(sequence_index, rows)
            return row_index + 1, column_index + 1
        row_index, column_index = divmod(sequence_index, cols)
        return row_index + 1, column_index + 1

    def _scan_mode_description(self) -> str:
        if self.automation_config.scan_mode == SCAN_MODE_COL_TTB:
            return (
                "top-to-bottom in each column, "
                "then return to row 1 and step to the next column"
            )
        return (
            "left-to-right in each row, "
            "then return to column 1 and step to the next row"
        )

    def _compute_relative_move_sequence(
        self, sequence_index: int
    ) -> List[Tuple[str, float, float]]:
        """Return a list of ``(label, dx_um, dy_um)`` moves from the current
        site to the next one."""
        current_row, current_col = self._site_row_col(sequence_index)
        next_row, next_col = self._site_row_col(sequence_index + 1)

        if self.automation_config.scan_mode == SCAN_MODE_COL_TTB:
            if next_col == current_col:
                dy_um = -(next_row - current_row) * self.automation_config.y_step_um
                return [("Moving to next row", 0.0, dy_um)]
            moves: List[Tuple[str, float, float]] = []
            reset_dy_um = (current_row - 1) * self.automation_config.y_step_um
            if abs(reset_dy_um) > 0.0:
                moves.append(("Returning to first row", 0.0, reset_dy_um))
            column_step_um = (next_col - current_col) * self.automation_config.x_step_um
            if abs(column_step_um) > 0.0 or not moves:
                moves.append((f"Advancing to column {next_col}", column_step_um, 0.0))
            return moves

        if next_row == current_row:
            dx_um = (next_col - current_col) * self.automation_config.x_step_um
            return [("Moving to next column", dx_um, 0.0)]

        moves = []
        reset_dx_um = -(current_col - 1) * self.automation_config.x_step_um
        if abs(reset_dx_um) > 0.0:
            moves.append(("Returning to first column", reset_dx_um, 0.0))
        row_step_um = (next_row - current_row) * self.automation_config.y_step_um
        if abs(row_step_um) > 0.0 or not moves:
            moves.append((f"Advancing to row {next_row}", 0.0, row_step_um))
        return moves

    # ------------------------------------------------------------------
    # Stage motion
    # ------------------------------------------------------------------

    def _move_to_next_site(self, sequence_index: int, site_number: int) -> bool:
        """Lift tips, step to the next site, lower tips.  Returns ``False``
        if a stop was requested during the operation."""
        self.log("Lifting probe tips")
        self.log(f"Stage reply: {self.stage.lift(self.stage_config.lift_command)}")
        if not self._wait_with_stop(self.stage_config.settle_after_up_s):
            return False

        if self.automation_config.use_relative_move:
            for move_label, dx_um, dy_um in self._compute_relative_move_sequence(
                sequence_index
            ):
                self.log(f"{move_label}: dX={dx_um:g} µm, dY={dy_um:g} µm")
                move_reply = self.stage.move_next(
                    dx_um=dx_um,
                    dy_um=dy_um,
                    use_relative_move=True,
                    move_template=self.stage_config.move_command_template,
                    next_site_command=self.stage_config.next_site_command,
                    site=site_number + 1,
                )
                self.log(f"Stage reply: {move_reply}")
                if not self._wait_with_stop(self.stage_config.settle_after_move_s):
                    return False
        else:
            self.log("Moving to next wafer position")
            move_reply = self.stage.move_next(
                dx_um=self.automation_config.x_step_um,
                dy_um=self.automation_config.y_step_um,
                use_relative_move=False,
                move_template=self.stage_config.move_command_template,
                next_site_command=self.stage_config.next_site_command,
                site=site_number + 1,
            )
            self.log(f"Stage reply: {move_reply}")
            if not self._wait_with_stop(self.stage_config.settle_after_move_s):
                return False

        self.log("Lowering probe tips")
        self.log(f"Stage reply: {self.stage.lower(self.stage_config.lower_command)}")
        return self._wait_with_stop(self.stage_config.settle_after_down_s)

    # ------------------------------------------------------------------
    # CSV helpers
    # ------------------------------------------------------------------

    def _save_site_csv(
        self,
        output_dir: Path,
        site_number: int,
        row_number: int,
        column_number: int,
        data: List[MeasurementPoint],
        rollover_result: RolloverResult,
    ) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = output_dir / (
            f"{self.automation_config.device_name}"
            f"_row_{row_number:02d}_col_{column_number:02d}"
            f"_site_{site_number:03d}_{timestamp}.csv"
        )
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "Site", "Row", "Column", "Point",
                "Timestamp", "Relative_Time_s", "Setpoint",
                "Voltage_V", "Current_A", "Optical_Power_W",
                "Status",
                "Rollover_Detected", "Stop_Reason",
                "Peak_Power_W", "Peak_Voltage_V", "Peak_Current_A",
                "Peak_Point_Index",
            ])
            rollover_flag = str(rollover_result.detected) if rollover_result else "False"
            stop_reason   = rollover_result.stop_reason   if rollover_result else "sweep_complete"
            peak_power    = rollover_result.peak_power    if rollover_result else 0.0
            peak_voltage  = rollover_result.peak_voltage  if rollover_result else 0.0
            peak_current  = rollover_result.peak_current  if rollover_result else 0.0
            peak_index    = rollover_result.peak_point_index if rollover_result else -1
            for point in data:
                writer.writerow([
                    site_number, row_number, column_number,
                    point.point_index,
                    datetime.fromtimestamp(point.timestamp).isoformat(),
                    f"{point.relative_time:.6f}",
                    f"{point.setpoint:.6e}",
                    f"{point.voltage:.6e}",
                    f"{point.current:.6e}",
                    f"{point.optical_power:.6e}",
                    point.status,
                    rollover_flag, stop_reason,
                    f"{peak_power:.6e}",
                    f"{peak_voltage:.6e}",
                    f"{peak_current:.6e}",
                    peak_index,
                ])
        return str(path)

    def _save_summary_csv(self, output_dir: Path) -> str:
        path = output_dir / f"{self.automation_config.device_name}_summary.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "Site", "Row", "Column", "Points",
                "Max_Current_A", "Max_Power_W",
                "Rollover_Detected", "Stop_Reason",
                "Peak_Power_W", "Peak_Voltage_V", "Peak_Current_A",
                "Peak_Point_Index",
                "CSV_Path", "Hook_Path", "Status",
            ])
            for item in self.site_summaries:
                writer.writerow([
                    item.site_number, item.row_number, item.column_number,
                    item.points,
                    f"{item.max_current_a:.6e}",
                    f"{item.max_power_w:.6e}",
                    item.rollover_detected,
                    item.stop_reason,
                    f"{item.peak_power_w:.6e}",
                    f"{item.peak_voltage_v:.6e}",
                    f"{item.peak_current_a:.6e}",
                    item.peak_point_index,
                    item.csv_path,
                    item.hook_path,
                    item.status,
                ])
        return str(path)

    def _write_spectrometer_hook(
        self,
        output_dir: Path,
        site_number: int,
        row_number: int,
        column_number: int,
        csv_path: str,
    ) -> str:
        hook_cfg = self.spectrometer_hook
        if hook_cfg.action == "Disabled":
            return ""
        hook_path = output_dir / (
            f"{self.automation_config.device_name}"
            f"_row_{row_number:02d}_col_{column_number:02d}"
            f"_site_{site_number:03d}_spectrometer_hook.json"
        )
        payload = {
            "site": site_number,
            "row": row_number,
            "column": column_number,
            "device_name": self.automation_config.device_name,
            "measurement_csv": csv_path,
            "spectrometer_script": str(
                Path(__file__).parent.parent / "b1500_spectrometer_synchronized.py"
            ),
            "num_channels": 1,
            "smu_anode": self.sweep_config.smu,
            "spec_integration_time_ms": hook_cfg.integration_time_ms,
            "spec_num_averages": hook_cfg.averages,
            "use_hardware_sync": hook_cfg.use_hardware_sync,
            "timed_bias_during_spectrum": hook_cfg.timed_bias_during_spectrum,
            "timed_bias_interval_ms": hook_cfg.timed_bias_interval_ms,
            "created_at": datetime.now().isoformat(),
        }
        hook_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.log(f"Spectrometer hook config written: {hook_path}")
        if hook_cfg.action == "Write config and launch spectrometer GUI":
            script_path = (
                Path(__file__).parent.parent / "b1500_spectrometer_synchronized.py"
            )
            subprocess.Popen([sys.executable, str(script_path)])
            self.log("Launched b1500_spectrometer_synchronized.py")
        return str(hook_path)

    # ------------------------------------------------------------------
    # Main automation loop
    # ------------------------------------------------------------------

    def run(self) -> List[SiteSummary]:
        """Execute the full wafer automation run.

        Sequence per site
        -----------------
        1. [Before site 1] Lower probe tips.
        2. Run B1500 + power-meter sweep with rollover detection.
        3. Save per-site CSV.
        4. [Between sites] Lift → move → lower.
        5. [After last site] Lift probe tips.
        6. Save summary CSV.
        """
        self.running = True
        self.stop_requested = False
        self.site_summaries = []

        output_dir = Path(self.automation_config.output_folder)
        if not output_dir.is_absolute():
            output_dir = Path(__file__).parent.parent / output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        total_sites = max(1, self.automation_config.total_sites)
        self.log(
            f"Starting wafer automation with rollover detection "
            f"for {total_sites} site(s)"
        )
        self.log(
            f"Wafer map: {max(1, self.automation_config.map_rows)} row(s) × "
            f"{max(1, self.automation_config.map_cols)} col(s), "
            f"{self._scan_mode_description()}"
        )
        if self.sweep_config.enable_rollover:
            self.log(
                f"Rollover detection enabled — "
                f"method={self.sweep_config.rollover_method}, "
                f"threshold={self.sweep_config.rollover_threshold * 100:.0f}% of peak, "
                f"window={self.sweep_config.rollover_window}"
            )
        else:
            self.log("Rollover detection DISABLED — full sweep for every site")

        map_capacity = max(
            1, self.automation_config.map_rows * self.automation_config.map_cols
        )
        if total_sites > map_capacity:
            self.log(
                f"Note: total sites ({total_sites}) exceeds map capacity "
                f"({map_capacity}).  Continuing with virtual rows/columns."
            )

        if not self.b1500.connected:
            self.log(
                "B1500 is not connected — "
                "continuing with simulated electrical data (I=0)"
            )

        if total_sites > 1 and not self.stage.connected:
            self.log("Stage is not connected. Only one site can be measured.")
            total_sites = 1

        try:
            # ---- Step 0: lower tips onto site 1 ----
            if self.stage.connected:
                self.log("--- Step 1: Lowering probe tips onto site 1 ---")
                try:
                    reply = self.stage.lower(self.stage_config.lower_command)
                    self.log(f"Stage reply: {reply}")
                    if not self._wait_with_stop(self.stage_config.settle_after_down_s):
                        self.log(
                            "Stop requested during initial tip-lower settle — aborting"
                        )
                        self.running = False
                        return self.site_summaries
                except Exception as lower_exc:
                    self.log(
                        f"WARNING: Initial tip-lower failed: {lower_exc} "
                        "— continuing anyway"
                    )

            # ---- Main site loop ----
            for idx in range(total_sites):
                if self.stop_requested:
                    self.log("Stop requested by user")
                    if self.stage.connected:
                        try:
                            self.log("Lifting probe tips (stop requested)")
                            self.log(
                                f"Stage reply: "
                                f"{self.stage.lift(self.stage_config.lift_command)}"
                            )
                            self._wait_with_stop(self.stage_config.settle_after_up_s)
                        except Exception as lift_exc:
                            self.log(f"WARNING: Tip-lift after stop failed: {lift_exc}")
                    break

                site_number = self.automation_config.start_site + idx
                sequence_index = self._sequence_index(idx)
                row_number, column_number = self._site_row_col(sequence_index)
                self.log(
                    f"Measuring site {site_number} "
                    f"(row {row_number}, col {column_number}) "
                    f"({idx + 1}/{total_sites})"
                )

                site_cfg = replace(
                    self.sweep_config,
                    autosave=False,
                    output_folder=str(output_dir),
                    device_name=(
                        f"{self.automation_config.device_name}"
                        f"_row_{row_number:02d}"
                        f"_col_{column_number:02d}"
                        f"_site_{site_number:03d}"
                    ),
                )

                measurement_engine = SynchronizedMeasurementEngine(
                    self.b1500, self.power_meter, site_cfg
                )

                _rollover_holder: List[RolloverResult] = [RolloverResult()]

                def _rollover_cb(
                    result: RolloverResult,
                    _sn: int = site_number,
                    _h: List[RolloverResult] = _rollover_holder,
                ) -> None:
                    _h[0] = result
                    if result.detected:
                        self.log(
                            f"[Site {_sn}] ROLLOVER DETECTED at point "
                            f"{result.peak_point_index} — "
                            f"peak power {result.peak_power:.4e} W at "
                            f"{result.peak_voltage:.4f} V / "
                            f"{result.peak_current:.4e} A"
                        )
                    else:
                        self.log(
                            f"[Site {_sn}] Sweep ended — "
                            f"stop_reason={result.stop_reason}, "
                            f"peak power {result.peak_power:.4e} W"
                        )
                    if self.on_rollover_detected:
                        self.on_rollover_detected(_sn, result)

                measurement_engine.on_rollover_detected = _rollover_cb

                def _point_cb(
                    point: MeasurementPoint,
                    _eng=measurement_engine,
                ) -> None:
                    if self.on_point_complete:
                        self.on_point_complete((site_number, point))
                    if self.stop_requested:
                        _eng.stop()

                measurement_engine.on_point_complete = _point_cb
                measurement_engine.on_log = (
                    lambda msg, s=site_number: self.log(f"[Site {s}] {msg}")
                )

                data = measurement_engine.run()
                rollover_result = _rollover_holder[0]

                self.log(f"Measurement finished for site {site_number}")
                csv_path = self._save_site_csv(
                    output_dir, site_number, row_number, column_number,
                    data, rollover_result,
                )

                summary = SiteSummary(
                    site_number=site_number,
                    row_number=row_number,
                    column_number=column_number,
                    points=len(data),
                    max_current_a=max(
                        (abs(p.current) for p in data), default=0.0
                    ),
                    max_power_w=max(
                        (p.optical_power for p in data), default=0.0
                    ),
                    csv_path=csv_path,
                    hook_path=self._write_spectrometer_hook(
                        output_dir, site_number, row_number, column_number,
                        csv_path,
                    ),
                    status="OK" if data else "NO_DATA",
                    rollover_detected=rollover_result.detected,
                    stop_reason=rollover_result.stop_reason,
                    peak_power_w=rollover_result.peak_power,
                    peak_voltage_v=rollover_result.peak_voltage,
                    peak_current_a=rollover_result.peak_current,
                    peak_point_index=rollover_result.peak_point_index,
                )
                self.site_summaries.append(summary)
                if self.on_site_complete:
                    self.on_site_complete(summary)
                if self.on_progress:
                    self.on_progress(idx + 1, total_sites)

                last_site = (idx >= total_sites - 1) or self.stop_requested
                if last_site:
                    if self.stage.connected:
                        try:
                            self.log(
                                "--- Lifting probe tips (end of automation) ---"
                            )
                            self.log(
                                f"Stage reply: "
                                f"{self.stage.lift(self.stage_config.lift_command)}"
                            )
                            self._wait_with_stop(self.stage_config.settle_after_up_s)
                        except Exception as lift_exc:
                            self.log(f"WARNING: Final tip-lift failed: {lift_exc}")
                    if self.stop_requested:
                        self.log("Stop requested by user")
                    break

                if not self._move_to_next_site(sequence_index, site_number):
                    break

            summary_path = self._save_summary_csv(output_dir)
            self.log(f"Summary saved to {summary_path}")

        except Exception as exc:
            self.log(f"Automation error: {exc}")
            if self.stage.connected:
                try:
                    self.log("Lifting probe tips (error recovery)")
                    self.stage.lift(self.stage_config.lift_command)
                except Exception:
                    pass
        finally:
            self.running = False

        return self.site_summaries
