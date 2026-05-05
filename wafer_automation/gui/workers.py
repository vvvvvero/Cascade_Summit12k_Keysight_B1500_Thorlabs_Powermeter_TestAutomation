"""
workers.py
==========
PyQt5 QThread workers that wrap the automation engine and VISA scanner so
the GUI stays responsive during long-running operations.
"""

from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from b1500_powermeter_rollover import (
    B1500Controller,
    RolloverResult,
)

from ..engine import WaferAutomationEngine
from ..models import SiteSummary
from ..stage_controller import CascadeNucleusController


class AutomationWorker(QThread):
    """Runs :class:`WaferAutomationEngine` in a background thread and
    re-emits all engine callbacks as Qt signals."""

    progress          = pyqtSignal(int, int)         # (current, total)
    log_message       = pyqtSignal(str)
    point_complete    = pyqtSignal(object)           # (site_number, MeasurementPoint)
    site_complete     = pyqtSignal(object)           # SiteSummary
    rollover_detected = pyqtSignal(int, object)      # (site_number, RolloverResult)
    finished_signal   = pyqtSignal()

    def __init__(self, engine: WaferAutomationEngine) -> None:
        super().__init__()
        self.engine = engine
        self.engine.on_log          = lambda msg: self.log_message.emit(msg)
        self.engine.on_progress     = lambda cur, total: self.progress.emit(cur, total)
        self.engine.on_point_complete = lambda payload: self.point_complete.emit(payload)
        self.engine.on_site_complete  = lambda item: self.site_complete.emit(item)
        self.engine.on_rollover_detected = (
            lambda sn, res: self.rollover_detected.emit(sn, res)
        )

    def run(self) -> None:
        self.engine.run()
        self.finished_signal.emit()


class _ResourceScanWorker(QThread):
    """Enumerates VISA resources in a background thread.

    Running off the main thread has two benefits:
    1. The GUI stays responsive during the (potentially slow) VISA scan.
    2. The NI-VISA driver is loaded and fully initialised in the background,
       so by the time the user clicks Connect the first ``open_resource()``
       call succeeds immediately.
    """

    resources_ready = pyqtSignal(list, list, list)   # gpib, usb, stage_visa
    scan_log        = pyqtSignal(str)

    def __init__(
        self,
        b1500: B1500Controller,
        stage: CascadeNucleusController,
    ) -> None:
        super().__init__()
        self._b1500 = b1500
        self._stage = stage

    def run(self) -> None:
        try:
            all_resources: List[str] = self._b1500.list_all_resources()
        except BaseException as exc:
            all_resources = []
            self.scan_log.emit(f"B1500 resource scan skipped: {exc}")

        gpib = [r for r in all_resources if "GPIB" in r.upper()]
        usb  = [r for r in all_resources if "USB"  in r.upper()]

        try:
            stage_visa: List[str] = self._stage.list_resources()
        except BaseException as exc:
            stage_visa = []
            self.scan_log.emit(f"Stage resource scan skipped: {exc}")

        self.resources_ready.emit(gpib, usb, stage_visa)
