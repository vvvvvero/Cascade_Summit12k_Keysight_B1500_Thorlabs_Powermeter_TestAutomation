"""
main_window.py
==============
WaferAutomationRolloverGUI — main PyQt5 window for the Cascade Summit +
Keysight B1500 wafer automation tool with per-site rollover detection.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import List, Optional

import matplotlib
matplotlib.use("Qt5Agg")
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from b1500_powermeter_rollover import (
    B1500Controller,
    RolloverResult,
    SweepConfig,
    ThorlabsPowerMeterController,
)

# Help PyQt find the Windows platform plugin when running from a venv.
try:
    _qt_plugin_root = (
        Path(sys.executable).resolve().parent.parent
        / "Lib" / "site-packages" / "PyQt5" / "Qt5" / "plugins"
    )
    _qt_platforms = _qt_plugin_root / "platforms"
    if _qt_platforms.exists():
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(_qt_platforms))
        os.environ.setdefault("QT_PLUGIN_PATH", str(_qt_plugin_root))
except Exception:
    pass

from ..constants import (
    CASCADE_CURRENT_DIE_QUERY_COMMAND,
    CASCADE_CURRENT_SUBSITE_QUERY_COMMAND,
    CASCADE_FIRST_DIE_COMMAND,
    CASCADE_NEXT_DIE_COMMAND,
    CASCADE_POSITION_QUERY_COMMAND,
    NUCLEUS_HOME_OPTIONS,
    NUCLEUS_MOVE_OPTIONS,
    NUCLEUS_NEXT_SITE_OPTIONS,
    NUCLEUS_SET_REF_DIE_OPTIONS,
    NUCLEUS_TIP_DOWN_OPTIONS,
    NUCLEUS_TIP_UP_OPTIONS,
    ROLLOVER_METHODS,
    SCAN_MODE_COL_TTB,
    SCAN_MODE_ROW_LTR,
    SPECTROMETER_HOOK_ACTIONS,
)
from ..engine import WaferAutomationEngine
from ..models import (
    AutomationConfig,
    SiteSummary,
    SpectrometerHookConfig,
    StageConfig,
)
from ..stage_controller import CascadeNucleusController
from .workers import AutomationWorker, _ResourceScanWorker


class WaferAutomationRolloverGUI(QMainWindow):
    """Main application window."""

    # TCP address hints shown when "TCP Socket" transport is selected
    _TCP_ADDR_HINTS = ["127.0.0.1:8765", "127.0.0.1:23", "localhost:8765"]

    def __init__(self) -> None:
        super().__init__()
        self.b1500 = B1500Controller()
        self.power_meter = ThorlabsPowerMeterController()
        self.stage = CascadeNucleusController()
        self.worker: Optional[AutomationWorker] = None
        self._stage_visa_resources: List[str] = []
        self._scan_worker: Optional[_ResourceScanWorker] = None

        self.plot_voltages: List[float] = []
        self.plot_currents: List[float] = []
        self.plot_powers:   List[float] = []

        self.setWindowTitle(
            "Cascade Summit + B1500 Wafer Automation with Rollover Detection"
            " — © Veronica GaoZhan"
        )
        self.setMinimumSize(1560, 960)
        self._build_ui()
        self.refresh_resources()

    # ------------------------------------------------------------------
    # UI construction helpers
    # ------------------------------------------------------------------

    def _make_command_combo(
        self, options: List[str], default: str, tooltip: str = ""
    ) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(options)
        combo.setCurrentText(default)
        if tooltip:
            combo.setToolTip(tooltip)
        return combo

    @staticmethod
    def _is_position_query_command(command: str) -> bool:
        parts = command.strip().split()
        return bool(parts) and parts[0].lower() == CASCADE_POSITION_QUERY_COMMAND

    @staticmethod
    def _is_die_query_command(command: str) -> bool:
        parts = command.strip().split()
        return bool(parts) and parts[0].lower() == CASCADE_CURRENT_DIE_QUERY_COMMAND

    @staticmethod
    def _is_subsite_query_command(command: str) -> bool:
        parts = command.strip().split()
        return bool(parts) and parts[0].lower() == CASCADE_CURRENT_SUBSITE_QUERY_COMMAND

    @staticmethod
    def _parse_numeric_reply(reply: str) -> List[float]:
        return [
            float(m)
            for m in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", reply)
        ]

    @classmethod
    def _format_stage_query_reply(cls, command: str, reply: str) -> Optional[str]:
        values = cls._parse_numeric_reply(reply)
        if cls._is_position_query_command(command):
            if len(values) < 3:
                return None
            return f"Stage position [X, Y, Z]: {values[0]:g}, {values[1]:g}, {values[2]:g}"
        if cls._is_die_query_command(command):
            if len(values) < 2:
                return None
            return f"Current die [X, Y]: {values[0]:g}, {values[1]:g}"
        if cls._is_subsite_query_command(command):
            if len(values) < 2:
                return None
            return f"Current subsite [X, Y]: {values[0]:g}, {values[1]:g}"
        return None

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setStyleSheet(
            "QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox "
            "{ min-height: 34px; padding: 4px 8px; }"
            "QComboBox { min-width: 180px; }"
            "QLineEdit { min-width: 220px; }"
        )

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(640)
        left = QWidget()
        left.setMinimumWidth(620)
        left_layout = QVBoxLayout(left)

        # ---- Section 1: Device connections --------------------------------
        device_group = QGroupBox("1. Device Connections")
        dg = QGridLayout(device_group)

        dg.addWidget(QLabel("B1500"), 0, 0)
        self.combo_b1500 = QComboBox()
        self.combo_b1500.setEditable(True)
        dg.addWidget(self.combo_b1500, 0, 1)
        self.btn_b1500 = QPushButton("Connect")
        self.btn_b1500.clicked.connect(self.connect_b1500)
        dg.addWidget(self.btn_b1500, 0, 2)

        dg.addWidget(QLabel("Power meter"), 1, 0)
        self.combo_pm = QComboBox()
        self.combo_pm.setEditable(True)
        dg.addWidget(self.combo_pm, 1, 1)
        self.btn_pm = QPushButton("Connect")
        self.btn_pm.clicked.connect(self.connect_power_meter)
        dg.addWidget(self.btn_pm, 1, 2)

        dg.addWidget(QLabel("Stage transport"), 2, 0)
        self.combo_stage_transport = QComboBox()
        self.combo_stage_transport.addItems(["TCP Socket", "Simulation", "VISA"])
        self.combo_stage_transport.currentIndexChanged.connect(
            self._update_stage_addr_for_transport
        )
        dg.addWidget(self.combo_stage_transport, 2, 1)

        dg.addWidget(QLabel("Stage address"), 3, 0)
        self.combo_stage_addr = QComboBox()
        self.combo_stage_addr.setEditable(True)
        dg.addWidget(self.combo_stage_addr, 3, 1)
        self.btn_stage = QPushButton("Connect")
        self.btn_stage.clicked.connect(self.connect_stage)
        dg.addWidget(self.btn_stage, 3, 2)

        self.btn_refresh = QPushButton("Refresh Devices")
        self.btn_refresh.clicked.connect(self.refresh_resources)
        dg.addWidget(self.btn_refresh, 4, 0, 1, 3)
        left_layout.addWidget(device_group)

        # ---- Section 2: Measurement settings ------------------------------
        sweep_group = QGroupBox("2. Measurement Settings")
        sg = QGridLayout(sweep_group)

        sg.addWidget(QLabel("SMU"), 0, 0)
        self.spin_smu = QSpinBox()
        self.spin_smu.setRange(1, 10)
        self.spin_smu.setValue(1)
        sg.addWidget(self.spin_smu, 0, 1)

        sg.addWidget(QLabel("Mode"), 0, 2)
        self.combo_mode = QComboBox()
        self.combo_mode.addItems([
            "IV (Source V / Measure I)", "VI (Source I / Measure V)"
        ])
        self.combo_mode.currentIndexChanged.connect(self.update_compliance_unit)
        sg.addWidget(self.combo_mode, 0, 3)

        sg.addWidget(QLabel("Start"), 1, 0)
        self.spin_start = QDoubleSpinBox()
        self.spin_start.setRange(-200.0, 200.0)
        self.spin_start.setDecimals(4)
        self.spin_start.setValue(0.0)
        sg.addWidget(self.spin_start, 1, 1)

        sg.addWidget(QLabel("Stop"), 1, 2)
        self.spin_stop = QDoubleSpinBox()
        self.spin_stop.setRange(-200.0, 200.0)
        self.spin_stop.setDecimals(4)
        self.spin_stop.setValue(2.0)
        sg.addWidget(self.spin_stop, 1, 3)

        sg.addWidget(QLabel("Steps"), 2, 0)
        self.spin_steps = QSpinBox()
        self.spin_steps.setRange(2, 5000)
        self.spin_steps.setValue(21)
        sg.addWidget(self.spin_steps, 2, 1)

        sg.addWidget(QLabel("Dwell [s]"), 2, 2)
        self.spin_dwell = QDoubleSpinBox()
        self.spin_dwell.setRange(0.0, 10.0)
        self.spin_dwell.setDecimals(3)
        self.spin_dwell.setValue(0.1)
        sg.addWidget(self.spin_dwell, 2, 3)

        sg.addWidget(QLabel("Compliance"), 3, 0)
        self.spin_compliance = QDoubleSpinBox()
        self.spin_compliance.setRange(0.0, 200.0)
        self.spin_compliance.setDecimals(6)
        self.spin_compliance.setValue(0.1)
        sg.addWidget(self.spin_compliance, 3, 1)
        self.update_compliance_unit()

        self.check_two_dir = QCheckBox("Two-direction sweep")
        sg.addWidget(self.check_two_dir, 3, 2)

        self.check_enable_pm = QCheckBox("Use power meter")
        self.check_enable_pm.setChecked(True)
        sg.addWidget(self.check_enable_pm, 3, 3)

        sg.addWidget(QLabel("Wavelength [nm]"), 4, 0)
        self.spin_wavelength = QDoubleSpinBox()
        self.spin_wavelength.setRange(200.0, 2000.0)
        self.spin_wavelength.setValue(850.0)
        sg.addWidget(self.spin_wavelength, 4, 1)

        sg.addWidget(QLabel("PM averages"), 4, 2)
        self.spin_pm_avg = QSpinBox()
        self.spin_pm_avg.setRange(1, 1000)
        self.spin_pm_avg.setValue(1)
        sg.addWidget(self.spin_pm_avg, 4, 3)

        left_layout.addWidget(sweep_group)

        # ---- Section 3: Rollover detection --------------------------------
        rollover_group = QGroupBox("3. Rollover Detection (Auto-Stop)")
        rg = QGridLayout(rollover_group)

        self.check_enable_rollover = QCheckBox(
            "Enable rollover detection and auto-stop"
        )
        self.check_enable_rollover.setChecked(True)
        self.check_enable_rollover.toggled.connect(self._update_rollover_controls)
        rg.addWidget(self.check_enable_rollover, 0, 0, 1, 4)

        rg.addWidget(QLabel("Method"), 1, 0)
        self.combo_rollover_method = QComboBox()
        self.combo_rollover_method.addItems(ROLLOVER_METHODS)
        self.combo_rollover_method.setCurrentText("cusum")
        self.combo_rollover_method.currentTextChanged.connect(
            self._update_rollover_controls
        )
        rg.addWidget(self.combo_rollover_method, 1, 1)

        rg.addWidget(QLabel("Threshold [% of peak]"), 1, 2)
        self.spin_rollover_threshold = QDoubleSpinBox()
        self.spin_rollover_threshold.setRange(1.0, 99.9)
        self.spin_rollover_threshold.setDecimals(1)
        self.spin_rollover_threshold.setValue(90.0)
        self.spin_rollover_threshold.setSuffix(" %")
        rg.addWidget(self.spin_rollover_threshold, 1, 3)

        rg.addWidget(QLabel("Window"), 2, 0)
        self.spin_rollover_window = QSpinBox()
        self.spin_rollover_window.setRange(2, 100)
        self.spin_rollover_window.setValue(5)
        self.spin_rollover_window.setToolTip(
            "Rolling-avg / EWMA / regression window size"
        )
        rg.addWidget(self.spin_rollover_window, 2, 1)

        rg.addWidget(QLabel("EWMA alpha"), 2, 2)
        self.spin_rollover_alpha = QDoubleSpinBox()
        self.spin_rollover_alpha.setRange(0.01, 1.0)
        self.spin_rollover_alpha.setDecimals(2)
        self.spin_rollover_alpha.setValue(0.3)
        self.spin_rollover_alpha.setToolTip(
            "EWMA decay factor α (only used with 'ewma' method)"
        )
        rg.addWidget(self.spin_rollover_alpha, 2, 3)

        rg.addWidget(QLabel("CUSUM slack [%]"), 3, 0)
        self.spin_cusum_slack = QDoubleSpinBox()
        self.spin_cusum_slack.setRange(0.01, 50.0)
        self.spin_cusum_slack.setDecimals(2)
        self.spin_cusum_slack.setValue(1.0)
        self.spin_cusum_slack.setSuffix(" %")
        self.spin_cusum_slack.setToolTip(
            "Per-step noise allowance as % of peak power (CUSUM only)"
        )
        rg.addWidget(self.spin_cusum_slack, 3, 1)

        rg.addWidget(QLabel("CUSUM h"), 3, 2)
        self.spin_cusum_h = QDoubleSpinBox()
        self.spin_cusum_h.setRange(0.05, 5.0)
        self.spin_cusum_h.setDecimals(2)
        self.spin_cusum_h.setValue(0.5)
        self.spin_cusum_h.setToolTip(
            "Decision interval as multiple of peak power (CUSUM only, typical 0.2–1.0)"
        )
        rg.addWidget(self.spin_cusum_h, 3, 3)

        lbl_rollover_note = QLabel(
            "cusum = fastest (1–2 pts reaction)  |  ewma = smooth  |  "
            "rolling_avg = classic window  |  regression = sklearn"
        )
        lbl_rollover_note.setStyleSheet("color: gray; font-size: 9pt;")
        lbl_rollover_note.setWordWrap(True)
        rg.addWidget(lbl_rollover_note, 4, 0, 1, 4)

        left_layout.addWidget(rollover_group)
        self._update_rollover_controls()

        # ---- Section 4: Wafer step automation -----------------------------
        auto_group = QGroupBox("4. Wafer Step Automation")
        ag = QGridLayout(auto_group)

        ag.addWidget(QLabel("Total sites"), 0, 0)
        self.spin_sites = QSpinBox()
        self.spin_sites.setRange(1, 10000)
        self.spin_sites.setValue(5)
        ag.addWidget(self.spin_sites, 0, 1)

        ag.addWidget(QLabel("Start site"), 0, 2)
        self.spin_start_site = QSpinBox()
        self.spin_start_site.setRange(1, 10000)
        self.spin_start_site.setValue(1)
        self.spin_start_site.setToolTip(
            "Site number assigned to the first measured position (file naming only).\n"
            "The current physical stage position is always treated as site 1 for movement."
        )
        ag.addWidget(self.spin_start_site, 0, 3)

        ag.addWidget(QLabel("X step [µm]"), 1, 0)
        self.spin_x_step = QDoubleSpinBox()
        self.spin_x_step.setRange(-100000.0, 100000.0)
        self.spin_x_step.setDecimals(3)
        self.spin_x_step.setValue(100.0)
        ag.addWidget(self.spin_x_step, 1, 1)

        ag.addWidget(QLabel("Y step [µm]"), 1, 2)
        self.spin_y_step = QDoubleSpinBox()
        self.spin_y_step.setRange(-100000.0, 100000.0)
        self.spin_y_step.setDecimals(3)
        self.spin_y_step.setValue(0.0)
        ag.addWidget(self.spin_y_step, 1, 3)

        self.check_relative_move = QCheckBox("Use relative X/Y move")
        self.check_relative_move.setChecked(True)
        ag.addWidget(self.check_relative_move, 2, 0, 1, 2)

        ag.addWidget(QLabel("Rows"), 2, 2)
        self.spin_map_rows = QSpinBox()
        self.spin_map_rows.setRange(1, 10000)
        self.spin_map_rows.setValue(5)
        ag.addWidget(self.spin_map_rows, 2, 3)

        ag.addWidget(QLabel("Columns"), 3, 0)
        self.spin_map_cols = QSpinBox()
        self.spin_map_cols.setRange(1, 10000)
        self.spin_map_cols.setValue(5)
        ag.addWidget(self.spin_map_cols, 3, 1)

        ag.addWidget(QLabel("Scan order"), 3, 2)
        self.combo_scan_mode = QComboBox()
        self.combo_scan_mode.addItem("Left to right by row", SCAN_MODE_ROW_LTR)
        self.combo_scan_mode.addItem("Top to bottom by column", SCAN_MODE_COL_TTB)
        ag.addWidget(self.combo_scan_mode, 3, 3)

        self.lbl_scan_pattern = QLabel(
            "Scan mode: left to right in each row, "
            "then return to column 1 and step to the next row"
        )
        self.lbl_scan_pattern.setWordWrap(True)
        self.lbl_scan_pattern.setStyleSheet("color: gray;")
        ag.addWidget(self.lbl_scan_pattern, 4, 0, 1, 4)
        self.combo_scan_mode.currentIndexChanged.connect(self.update_scan_pattern_label)

        self.lbl_row_hint = QLabel(
            "Rows and columns define the rectangular wafer-map shape; "
            "total sites can be smaller for a partial run"
        )
        self.lbl_row_hint.setWordWrap(True)
        self.lbl_row_hint.setStyleSheet("color: gray;")
        ag.addWidget(self.lbl_row_hint, 5, 0, 1, 4)

        ag.addWidget(QLabel("After lift [s]"), 6, 0)
        self.spin_wait_up = QDoubleSpinBox()
        self.spin_wait_up.setRange(0.0, 30.0)
        self.spin_wait_up.setValue(0.5)
        ag.addWidget(self.spin_wait_up, 6, 1)

        ag.addWidget(QLabel("After move [s]"), 6, 2)
        self.spin_wait_move = QDoubleSpinBox()
        self.spin_wait_move.setRange(0.0, 30.0)
        self.spin_wait_move.setValue(1.0)
        ag.addWidget(self.spin_wait_move, 6, 3)

        ag.addWidget(QLabel("After contact [s]"), 7, 0)
        self.spin_wait_down = QDoubleSpinBox()
        self.spin_wait_down.setRange(0.0, 30.0)
        self.spin_wait_down.setValue(0.5)
        ag.addWidget(self.spin_wait_down, 7, 1)

        self.update_scan_pattern_label()
        left_layout.addWidget(auto_group)

        # ---- Section 5: Stage / Nucleus commands --------------------------
        stage_group = QGroupBox("5. Stage / Nucleus Commands")
        cg = QGridLayout(stage_group)

        cg.addWidget(QLabel("Tips up"), 0, 0)
        self.combo_cmd_up = self._make_command_combo(NUCLEUS_TIP_UP_OPTIONS, ":mov:down")
        cg.addWidget(self.combo_cmd_up, 0, 1, 1, 2)

        cg.addWidget(QLabel("Tips down"), 1, 0)
        self.combo_cmd_down = self._make_command_combo(NUCLEUS_TIP_DOWN_OPTIONS, ":mov:up")
        cg.addWidget(self.combo_cmd_down, 1, 1, 1, 2)

        cg.addWidget(QLabel("Relative move template"), 2, 0)
        self.combo_cmd_move = self._make_command_combo(
            NUCLEUS_MOVE_OPTIONS,
            ":move:rel {dx_um} {dy_um} 0",
            "Placeholders: {dx_um}, {dy_um}, {dx_mm}, {dy_mm}, {site}",
        )
        cg.addWidget(self.combo_cmd_move, 2, 1, 1, 2)

        cg.addWidget(QLabel("Mapped next-site command"), 3, 0)
        self.combo_cmd_next = self._make_command_combo(
            NUCLEUS_NEXT_SITE_OPTIONS,
            ":mov:prob:next:die",
            "Optional wafer-map step command, e.g. :mov:prob:next:die",
        )
        cg.addWidget(self.combo_cmd_next, 3, 1, 1, 2)

        cg.addWidget(QLabel("Home command"), 4, 0)
        self.combo_cmd_home = self._make_command_combo(
            NUCLEUS_HOME_OPTIONS, ":mov:prob:firs:die"
        )
        cg.addWidget(self.combo_cmd_home, 4, 1, 1, 2)

        cg.addWidget(QLabel("Set 1st die cmd"), 5, 0)
        self.combo_cmd_set_ref_die = self._make_command_combo(
            NUCLEUS_SET_REF_DIE_OPTIONS,
            ":set:prob:firs:die",
            "Register the current physical position as die (1, 1) in the Cascade wafer map.",
        )
        cg.addWidget(self.combo_cmd_set_ref_die, 5, 1, 1, 2)

        self.btn_tip_up = QPushButton("Test Up")
        self.btn_tip_up.clicked.connect(
            lambda: self.run_stage_command(self.combo_cmd_up.currentText())
        )
        cg.addWidget(self.btn_tip_up, 6, 0)

        self.btn_tip_down = QPushButton("Test Down")
        self.btn_tip_down.clicked.connect(
            lambda: self.run_stage_command(self.combo_cmd_down.currentText())
        )
        cg.addWidget(self.btn_tip_down, 6, 1)

        self.btn_home = QPushButton("Home")
        self.btn_home.clicked.connect(
            lambda: self.run_stage_command(self.combo_cmd_home.currentText())
        )
        cg.addWidget(self.btn_home, 6, 2)

        self.btn_set_ref_die = QPushButton("Set as 1st Die")
        self.btn_set_ref_die.setToolTip(
            "Register the current physical stage position as die (1, 1).\n"
            "Move to the first die pad before clicking this button."
        )
        self.btn_set_ref_die.clicked.connect(
            lambda: self.run_stage_command(self.combo_cmd_set_ref_die.currentText())
        )
        cg.addWidget(self.btn_set_ref_die, 7, 0)

        self.btn_position_query = QPushButton("Position Query")
        self.btn_position_query.clicked.connect(
            lambda: self.run_stage_command(CASCADE_POSITION_QUERY_COMMAND)
        )
        cg.addWidget(self.btn_position_query, 7, 1)

        self.btn_first_die = QPushButton("Go to 1st Die")
        self.btn_first_die.setToolTip(
            "Move to the registered first-die position (does not change the reference)."
        )
        self.btn_first_die.clicked.connect(
            lambda: self.run_stage_command(CASCADE_FIRST_DIE_COMMAND)
        )
        cg.addWidget(self.btn_first_die, 7, 2)

        self.btn_next_die = QPushButton("Next Die")
        self.btn_next_die.clicked.connect(
            lambda: self.run_stage_command(CASCADE_NEXT_DIE_COMMAND)
        )
        cg.addWidget(self.btn_next_die, 8, 0)

        self.btn_current_die = QPushButton("Current Die")
        self.btn_current_die.clicked.connect(
            lambda: self.run_stage_command(CASCADE_CURRENT_DIE_QUERY_COMMAND)
        )
        cg.addWidget(self.btn_current_die, 8, 1)

        self.btn_current_subsite = QPushButton("Current Subsite")
        self.btn_current_subsite.clicked.connect(
            lambda: self.run_stage_command(CASCADE_CURRENT_SUBSITE_QUERY_COMMAND)
        )
        cg.addWidget(self.btn_current_subsite, 8, 2)

        left_layout.addWidget(stage_group)

        # ---- Section 6: Output / optional spectrometer hook ---------------
        out_group = QGroupBox("6. Output / Optional Spectrometer Hook")
        og = QGridLayout(out_group)

        og.addWidget(QLabel("Output folder"), 0, 0)
        self.edit_output = QLineEdit(
            str(Path(__file__).parent.parent.parent / "wafer_automation_results")
        )
        og.addWidget(self.edit_output, 0, 1)
        self.btn_browse = QPushButton("Browse")
        self.btn_browse.clicked.connect(self.browse_output)
        og.addWidget(self.btn_browse, 0, 2)

        og.addWidget(QLabel("Device name"), 1, 0)
        self.edit_device = QLineEdit("VCSEL")
        og.addWidget(self.edit_device, 1, 1, 1, 2)

        og.addWidget(QLabel("Hook action"), 2, 0)
        self.combo_spec_action = QComboBox()
        self.combo_spec_action.addItems(SPECTROMETER_HOOK_ACTIONS)
        og.addWidget(self.combo_spec_action, 2, 1, 1, 2)

        og.addWidget(QLabel("Integration [ms]"), 3, 0)
        self.spin_spec_integration = QDoubleSpinBox()
        self.spin_spec_integration.setRange(1.0, 60000.0)
        self.spin_spec_integration.setValue(100.0)
        og.addWidget(self.spin_spec_integration, 3, 1)

        og.addWidget(QLabel("Averages"), 3, 2)
        self.spin_spec_averages = QSpinBox()
        self.spin_spec_averages.setRange(1, 100)
        self.spin_spec_averages.setValue(1)
        og.addWidget(self.spin_spec_averages, 3, 3)

        self.check_hw_sync = QCheckBox("Use hardware sync")
        self.check_hw_sync.setChecked(True)
        og.addWidget(self.check_hw_sync, 4, 0, 1, 2)

        self.check_timed_bias = QCheckBox("Timed bias during spectrum")
        self.check_timed_bias.setChecked(True)
        og.addWidget(self.check_timed_bias, 4, 2, 1, 2)

        og.addWidget(QLabel("Timed interval [ms]"), 5, 0)
        self.spin_timed_interval = QDoubleSpinBox()
        self.spin_timed_interval.setRange(1.0, 1000.0)
        self.spin_timed_interval.setValue(10.0)
        og.addWidget(self.spin_timed_interval, 5, 1)

        lbl_spec_note = QLabel(
            "Settings mirror b1500_spectrometer_synchronized.py — 1-terminal mode"
        )
        lbl_spec_note.setStyleSheet("color: gray;")
        lbl_spec_note.setWordWrap(True)
        og.addWidget(lbl_spec_note, 6, 0, 1, 4)

        left_layout.addWidget(out_group)

        # ---- Start / Stop / Progress / Log --------------------------------
        button_row = QHBoxLayout()
        self.btn_start = QPushButton("Start Automation")
        self.btn_start.clicked.connect(self.start_automation)
        self.btn_start.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold;"
        )
        button_row.addWidget(self.btn_start)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self.stop_automation)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet(
            "background-color: #f44336; color: white; font-weight: bold;"
        )
        button_row.addWidget(self.btn_stop)
        left_layout.addLayout(button_row)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        left_layout.addWidget(self.progress)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(220)
        left_layout.addWidget(self.log_text)

        left_layout.addStretch(1)
        scroll.setWidget(left)
        root.addWidget(scroll)

        # ---- Right panel: plots + summary table ---------------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)

        self.figure = Figure(figsize=(10, 8))
        self.canvas = FigureCanvas(self.figure)
        self.ax_iv  = self.figure.add_subplot(2, 2, 1)
        self.ax_log = self.figure.add_subplot(2, 2, 2)
        self.ax_li  = self.figure.add_subplot(2, 2, 3)
        self.ax_pv  = self.figure.add_subplot(2, 2, 4)
        self._reset_axes()
        right_layout.addWidget(self.canvas)

        self.summary_table = QTableWidget(0, 10)
        self.summary_table.setHorizontalHeaderLabels([
            "Site", "Row", "Col", "Points",
            "Max I [A]", "Max P [W]",
            "Rollover?", "Stop Reason",
            "Peak P [W]", "Status",
        ])
        self.summary_table.horizontalHeader().setStretchLastSection(True)
        right_layout.addWidget(self.summary_table)

        root.addWidget(right, stretch=1)

        # ---- Status bar ---------------------------------------------------
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
        _copyright = QLabel(
            "© Veronica GaoZhan — Cascade Summit 12000 AP + Keysight B1500  "
            "1-Terminal Wafer Automation with Rollover Detection"
        )
        _copyright.setStyleSheet("color: gray; font-size: 9pt; padding-right: 6px;")
        self.status_bar.addPermanentWidget(_copyright)

    # ------------------------------------------------------------------
    # Dynamic UI helpers
    # ------------------------------------------------------------------

    def _update_rollover_controls(self) -> None:
        enabled  = self.check_enable_rollover.isChecked()
        method   = self.combo_rollover_method.currentText()
        is_cusum = method == "cusum"
        is_ewma  = method == "ewma"
        is_window = method in ("rolling_avg", "ewma", "regression")

        self.combo_rollover_method.setEnabled(enabled)
        self.spin_rollover_threshold.setEnabled(enabled)
        self.spin_rollover_window.setEnabled(enabled and is_window)
        self.spin_rollover_alpha.setEnabled(enabled and is_ewma)
        self.spin_cusum_slack.setEnabled(enabled and is_cusum)
        self.spin_cusum_h.setEnabled(enabled and is_cusum)

    def _reset_axes(self) -> None:
        for ax, title, xlabel, ylabel, yscale in [
            (self.ax_iv,  "I-V",      "Voltage [V]",  "Current [A]",       "linear"),
            (self.ax_log, "I-V log",  "Voltage [V]",  "|Current| [A]",     "log"),
            (self.ax_li,  "L-I",      "Current [A]",  "Optical Power [W]", "linear"),
            (self.ax_pv,  "P-V",      "Voltage [V]",  "Optical Power [W]", "linear"),
        ]:
            ax.clear()
            ax.set_title(title)
            ax.set_xlabel(xlabel)
            ax.set_ylabel(ylabel)
            ax.set_yscale(yscale)
            ax.grid(True, alpha=0.3)
        self.figure.tight_layout()

    def update_scan_pattern_label(self) -> None:
        scan_mode = self.combo_scan_mode.currentData()
        if scan_mode == SCAN_MODE_COL_TTB:
            text = (
                "Scan mode: top to bottom in each column, "
                "then return to row 1 and step to the next column"
            )
        else:
            text = (
                "Scan mode: left to right in each row, "
                "then return to column 1 and step to the next row"
            )
        self.lbl_scan_pattern.setText(text)

    def update_compliance_unit(self) -> None:
        suffix = " A" if self.combo_mode.currentIndex() == 0 else " V"
        self.spin_compliance.setSuffix(suffix)

    # ------------------------------------------------------------------
    # Transport-aware stage address combo
    # ------------------------------------------------------------------

    def _update_stage_addr_for_transport(self) -> None:
        transport    = self.combo_stage_transport.currentText()
        current_text = self.combo_stage_addr.currentText()
        self.combo_stage_addr.clear()
        if transport == "TCP Socket":
            self.combo_stage_addr.addItems(self._TCP_ADDR_HINTS)
            self.combo_stage_addr.setEnabled(True)
            self.combo_stage_addr.setToolTip(
                "Enter host:port of the Cascade Nucleus TCP server "
                "(e.g. 127.0.0.1:8765)"
            )
        elif transport == "VISA":
            self.combo_stage_addr.addItems(self._stage_visa_resources)
            self.combo_stage_addr.setEnabled(True)
            self.combo_stage_addr.setToolTip(
                "Select a VISA resource string for the stage "
                "(e.g. ASRL1::INSTR)"
            )
        else:  # Simulation
            self.combo_stage_addr.setEnabled(False)
            self.combo_stage_addr.setToolTip("No address needed in Simulation mode")
        if (
            current_text
            and current_text not in self._TCP_ADDR_HINTS
            and current_text not in self._stage_visa_resources
        ):
            self.combo_stage_addr.setEditText(current_text)

    # ------------------------------------------------------------------
    # Background VISA scan
    # ------------------------------------------------------------------

    def refresh_resources(self) -> None:
        self._update_stage_addr_for_transport()
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("Scanning\u2026")
        self.log("Scanning instruments (background)\u2026")
        self._scan_worker = _ResourceScanWorker(self.b1500, self.stage)
        self._scan_worker.scan_log.connect(self.log)
        self._scan_worker.resources_ready.connect(self._apply_resources)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.start()

    def _apply_resources(
        self, gpib: List[str], usb: List[str], stage_visa: List[str]
    ) -> None:
        self._stage_visa_resources = stage_visa
        self.combo_b1500.clear()
        self.combo_b1500.addItems(gpib)
        self.combo_pm.clear()
        self.combo_pm.addItems(usb)
        self._update_stage_addr_for_transport()
        self.log(
            f"Found {len(gpib)} GPIB resource(s), "
            f"{len(usb)} USB resource(s), "
            f"{len(stage_visa)} stage VISA candidate(s)"
        )

    def _on_scan_finished(self) -> None:
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText("Refresh Devices")

    # ------------------------------------------------------------------
    # Instrument connection
    # ------------------------------------------------------------------

    def connect_b1500(self) -> None:
        if self.b1500.connected:
            self.b1500.disconnect()
            self.btn_b1500.setText("Connect")
            self.btn_b1500.setToolTip("")
            self.log("B1500 disconnected")
            return
        resource = self.combo_b1500.currentText().strip()
        if not resource:
            QMessageBox.warning(self, "B1500", "Select a B1500 GPIB resource first.")
            return
        ok, msg = self.b1500.connect(resource)
        self.log(msg)
        if ok:
            idn_short = (
                self.b1500.idn.split(",")[1].strip()
                if self.b1500.idn
                else resource
            )
            self.btn_b1500.setText(f"Disconnect [{idn_short}]")
            self.btn_b1500.setToolTip(self.b1500.idn)
            self.status_bar.showMessage(f"B1500 connected: {self.b1500.idn}", 6000)

    def connect_power_meter(self) -> None:
        if self.power_meter.connected:
            self.power_meter.disconnect()
            self.btn_pm.setText("Connect")
            self.btn_pm.setToolTip("")
            self.log("Power meter disconnected")
            return
        resource = self.combo_pm.currentText().strip()
        if not resource:
            QMessageBox.warning(
                self, "Power meter", "Select a USB power meter resource first."
            )
            return
        ok, msg = self.power_meter.connect(resource)
        self.log(msg)
        if ok:
            idn_short = (
                self.power_meter.idn.split(",")[1].strip()
                if self.power_meter.idn
                else resource
            )
            self.btn_pm.setText(f"Disconnect [{idn_short}]")
            self.btn_pm.setToolTip(self.power_meter.idn)
            self.status_bar.showMessage(
                f"Power meter connected: {self.power_meter.idn}", 6000
            )

    def connect_stage(self) -> None:
        if self.stage.connected:
            self.stage.disconnect()
            self.btn_stage.setText("Connect")
            self.btn_stage.setToolTip("")
            self.log("Stage disconnected")
            return
        transport = self.combo_stage_transport.currentText()
        address   = self.combo_stage_addr.currentText().strip()

        if transport == "TCP Socket":
            if ":" not in address or address.upper().startswith(
                ("GPIB", "USB", "ASRL", "TCPIP", "VXI", "PXI")
            ):
                QMessageBox.warning(
                    self, "Stage",
                    f"Transport is 'TCP Socket' but the address looks wrong:\n"
                    f"  {address!r}\n\n"
                    "TCP Socket expects host:port, e.g. 127.0.0.1:8765",
                )
                return
        elif transport == "VISA":
            if ":" in address and not address.upper().startswith(
                ("GPIB", "USB", "ASRL", "TCPIP", "VXI", "PXI", "COM")
            ):
                QMessageBox.warning(
                    self, "Stage",
                    f"Transport is 'VISA' but the address looks like a TCP address:\n"
                    f"  {address!r}\n\n"
                    "Switch transport to 'TCP Socket' for IP/hostname connections, "
                    "or enter a VISA resource string (e.g. ASRL1::INSTR).",
                )
                return

        ok, msg = self.stage.connect(transport, address)
        self.log(msg)
        if ok:
            self.btn_stage.setText(f"Disconnect [{self.stage.idn}]")
            self.btn_stage.setToolTip(self.stage.idn)
            self.status_bar.showMessage(f"Stage connected: {self.stage.idn}", 6000)
            self.log(
                f"Stage: {transport} @ {address if address else '(no address)'}"
            )

    def run_stage_command(self, command: str) -> None:
        if not self.stage.connected:
            QMessageBox.warning(
                self, "Stage",
                "Connect the stage first.\n"
                "(Select 'Simulation' transport and click Connect to test without hardware.)",
            )
            return
        try:
            normalized = self.stage.preview_commands(command)
            norm_text  = " ; ".join(normalized)
            entered    = command.strip() or "<empty>"
            if norm_text and norm_text != entered:
                self.log(f"Sending stage command: {entered} -> {norm_text}")
            else:
                self.log(f"Sending stage command: {entered}")
            reply = self.stage.send_command(command)
            self.status_bar.showMessage(
                f"Stage command sent: {norm_text or entered}", 5000
            )
            formatted = self._format_stage_query_reply(command, reply)
            self.log(formatted if formatted is not None else f"Stage reply: {reply}")
        except Exception as exc:
            self.log(f"Stage error: {exc}")
            self.status_bar.showMessage(f"Stage error: {exc}", 5000)
            QMessageBox.warning(self, "Stage error", str(exc))

    # ------------------------------------------------------------------
    # Config builders
    # ------------------------------------------------------------------

    def get_sweep_config(self) -> SweepConfig:
        mode = "iv" if self.combo_mode.currentIndex() == 0 else "vi"
        return SweepConfig(
            smu=self.spin_smu.value(),
            mode=mode,
            start=self.spin_start.value(),
            stop=self.spin_stop.value(),
            steps=self.spin_steps.value(),
            dwell_s=self.spin_dwell.value(),
            two_direction=self.check_two_dir.isChecked(),
            compliance=self.spin_compliance.value(),
            enable_power_meter=self.check_enable_pm.isChecked(),
            power_wavelength_nm=self.spin_wavelength.value(),
            power_averages=self.spin_pm_avg.value(),
            output_folder=self.edit_output.text().strip(),
            device_name=self.edit_device.text().strip() or "VCSEL",
            autosave=False,
            enable_rollover=self.check_enable_rollover.isChecked(),
            rollover_method=self.combo_rollover_method.currentText(),
            rollover_threshold=self.spin_rollover_threshold.value() / 100.0,
            rollover_window=self.spin_rollover_window.value(),
            rollover_alpha=self.spin_rollover_alpha.value(),
            cusum_slack=self.spin_cusum_slack.value() / 100.0,
            cusum_h=self.spin_cusum_h.value(),
        )

    def get_stage_config(self) -> StageConfig:
        return StageConfig(
            transport=self.combo_stage_transport.currentText(),
            address=self.combo_stage_addr.currentText().strip(),
            lift_command=self.combo_cmd_up.currentText().strip(),
            lower_command=self.combo_cmd_down.currentText().strip(),
            move_command_template=self.combo_cmd_move.currentText().strip(),
            next_site_command=self.combo_cmd_next.currentText().strip(),
            home_command=self.combo_cmd_home.currentText().strip(),
            settle_after_up_s=self.spin_wait_up.value(),
            settle_after_move_s=self.spin_wait_move.value(),
            settle_after_down_s=self.spin_wait_down.value(),
        )

    def get_automation_config(self) -> AutomationConfig:
        return AutomationConfig(
            total_sites=self.spin_sites.value(),
            start_site=self.spin_start_site.value(),
            map_rows=self.spin_map_rows.value(),
            map_cols=self.spin_map_cols.value(),
            scan_mode=str(self.combo_scan_mode.currentData() or SCAN_MODE_ROW_LTR),
            x_step_um=self.spin_x_step.value(),
            y_step_um=self.spin_y_step.value(),
            use_relative_move=self.check_relative_move.isChecked(),
            output_folder=self.edit_output.text().strip(),
            device_name=self.edit_device.text().strip() or "VCSEL",
        )

    def get_spectrometer_hook_config(self) -> SpectrometerHookConfig:
        return SpectrometerHookConfig(
            action=self.combo_spec_action.currentText(),
            integration_time_ms=self.spin_spec_integration.value(),
            averages=self.spin_spec_averages.value(),
            use_hardware_sync=self.check_hw_sync.isChecked(),
            timed_bias_during_spectrum=self.check_timed_bias.isChecked(),
            timed_bias_interval_ms=self.spin_timed_interval.value(),
        )

    # ------------------------------------------------------------------
    # Automation control
    # ------------------------------------------------------------------

    def start_automation(self) -> None:
        if not self.b1500.connected:
            reply = QMessageBox.question(
                self,
                "B1500 not connected",
                "B1500 is not connected.\n"
                "Continue with simulated electrical data (V=setpoint, I=0)?\n\n"
                "This is useful for testing the stage sequence without hardware.",
            )
            if reply != QMessageBox.Yes:
                return

        if self.spin_sites.value() > 1 and not self.stage.connected:
            transport = self.combo_stage_transport.currentText()
            if transport == "Simulation":
                ok, msg = self.stage.connect(
                    transport, self.combo_stage_addr.currentText().strip()
                )
                if ok:
                    self.btn_stage.setText(f"Disconnect [{self.stage.idn}]")
                    self.btn_stage.setToolTip(self.stage.idn)
                    self.log(f"Stage auto-connected (Simulation): {self.stage.idn}")
                else:
                    QMessageBox.warning(
                        self, "Start", f"Stage auto-connect failed: {msg}"
                    )
                    return
            else:
                QMessageBox.warning(
                    self,
                    "Start",
                    "Connect the stage before running multi-site automation.",
                )
                return

        if self.check_enable_pm.isChecked() and not self.power_meter.connected:
            reply = QMessageBox.question(
                self,
                "Power meter not connected",
                "Power measurement is enabled, but no power meter is connected.\n"
                "Continue with electrical-only measurements?\n\n"
                "Note: rollover detection requires optical power data.",
            )
            if reply != QMessageBox.Yes:
                return

        self.plot_voltages = []
        self.plot_currents = []
        self.plot_powers   = []
        self.summary_table.setRowCount(0)
        self._reset_axes()

        engine = WaferAutomationEngine(
            self.b1500,
            self.power_meter,
            self.stage,
            self.get_sweep_config(),
            self.get_stage_config(),
            self.get_automation_config(),
            self.get_spectrometer_hook_config(),
        )
        self.worker = AutomationWorker(engine)
        self.worker.log_message.connect(self.log)
        self.worker.progress.connect(self.on_progress)
        self.worker.point_complete.connect(self.on_point_complete)
        self.worker.site_complete.connect(self.on_site_complete)
        self.worker.rollover_detected.connect(self.on_rollover_detected)
        self.worker.finished_signal.connect(self.on_finished)

        self.btn_b1500.setEnabled(False)
        self.btn_pm.setEnabled(False)
        self.btn_stage.setEnabled(False)
        self.btn_refresh.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress.setValue(0)
        self.worker.start()
        self.status_bar.showMessage("Automation running")

    def stop_automation(self) -> None:
        if self.worker and self.worker.engine:
            self.worker.engine.stop()
            self.log("Stop requested")

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def on_progress(self, current: int, total: int) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(current)
        self.status_bar.showMessage(f"Completed site {current} of {total}")

    def on_point_complete(self, payload) -> None:
        _, point = payload
        self.plot_voltages.append(point.voltage)
        self.plot_currents.append(point.current)
        self.plot_powers.append(point.optical_power)
        self.update_plots()

    def on_site_complete(self, summary: SiteSummary) -> None:
        from PyQt5.QtGui import QColor  # local import to avoid top-level Qt import order issues

        row = self.summary_table.rowCount()
        self.summary_table.insertRow(row)
        self.summary_table.setItem(row, 0, QTableWidgetItem(str(summary.site_number)))
        self.summary_table.setItem(row, 1, QTableWidgetItem(str(summary.row_number)))
        self.summary_table.setItem(row, 2, QTableWidgetItem(str(summary.column_number)))
        self.summary_table.setItem(row, 3, QTableWidgetItem(str(summary.points)))
        self.summary_table.setItem(row, 4, QTableWidgetItem(f"{summary.max_current_a:.3e}"))
        self.summary_table.setItem(row, 5, QTableWidgetItem(f"{summary.max_power_w:.3e}"))

        rollover_item = QTableWidgetItem("YES" if summary.rollover_detected else "no")
        if summary.rollover_detected:
            rollover_item.setBackground(QColor(255, 200, 200))
        self.summary_table.setItem(row, 6, rollover_item)

        self.summary_table.setItem(row, 7, QTableWidgetItem(summary.stop_reason))
        self.summary_table.setItem(row, 8, QTableWidgetItem(f"{summary.peak_power_w:.3e}"))

        status_item = QTableWidgetItem(summary.status)
        if summary.status != "OK":
            status_item.setBackground(QColor(255, 230, 180))
        self.summary_table.setItem(row, 9, status_item)

    def on_rollover_detected(self, site_number: int, result: RolloverResult) -> None:
        if result.detected:
            self.status_bar.showMessage(
                f"Site {site_number}: rollover at pt {result.peak_point_index} — "
                f"peak {result.peak_power:.3e} W",
                8000,
            )

    def _sync_instrument_buttons(self) -> None:
        if self.b1500.connected:
            idn_short = (
                self.b1500.idn.split(",")[1].strip()
                if "," in (self.b1500.idn or "")
                else (self.b1500.idn or "B1500")
            )
            self.btn_b1500.setText(f"Disconnect [{idn_short}]")
            self.btn_b1500.setToolTip(self.b1500.idn)
        else:
            self.btn_b1500.setText("Connect")
            self.btn_b1500.setToolTip("")

        if self.power_meter.connected:
            idn_short = (
                self.power_meter.idn.split(",")[1].strip()
                if "," in (self.power_meter.idn or "")
                else (self.power_meter.idn or "Power Meter")
            )
            self.btn_pm.setText(f"Disconnect [{idn_short}]")
            self.btn_pm.setToolTip(self.power_meter.idn)
        else:
            self.btn_pm.setText("Connect")
            self.btn_pm.setToolTip("")

        if self.stage.connected:
            self.btn_stage.setText(f"Disconnect [{self.stage.idn}]")
            self.btn_stage.setToolTip(self.stage.idn)
        else:
            self.btn_stage.setText("Connect")
            self.btn_stage.setToolTip("")

    def on_finished(self) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_b1500.setEnabled(True)
        self.btn_pm.setEnabled(True)
        self.btn_stage.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self._sync_instrument_buttons()
        self.status_bar.showMessage("Automation finished")
        self.log("Wafer automation finished")

    def log(self, message: str) -> None:
        self.log_text.append(message)
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    def browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select output folder", self.edit_output.text()
        )
        if folder:
            self.edit_output.setText(folder)

    # ------------------------------------------------------------------
    # Live plots
    # ------------------------------------------------------------------

    def update_plots(self) -> None:
        if not self.plot_voltages:
            return
        v = np.array(self.plot_voltages)
        i = np.array(self.plot_currents)
        p = np.array(self.plot_powers)

        self.ax_iv.clear()
        self.ax_iv.plot(v, i, "b.-")
        self.ax_iv.set_title("I-V")
        self.ax_iv.set_xlabel("Voltage [V]")
        self.ax_iv.set_ylabel("Current [A]")
        self.ax_iv.grid(True, alpha=0.3)

        self.ax_log.clear()
        i_abs = np.abs(i)
        i_abs[i_abs < 1e-15] = 1e-15
        self.ax_log.semilogy(v, i_abs, "m.-")
        self.ax_log.set_title("I-V log")
        self.ax_log.set_xlabel("Voltage [V]")
        self.ax_log.set_ylabel("|Current| [A]")
        self.ax_log.grid(True, alpha=0.3)

        self.ax_li.clear()
        self.ax_li.plot(i, p, "r.-")
        self.ax_li.set_title("L-I")
        self.ax_li.set_xlabel("Current [A]")
        self.ax_li.set_ylabel("Optical Power [W]")
        self.ax_li.grid(True, alpha=0.3)

        self.ax_pv.clear()
        self.ax_pv.plot(v, p, "g.-")
        self.ax_pv.set_title("P-V")
        self.ax_pv.set_xlabel("Voltage [V]")
        self.ax_pv.set_ylabel("Optical Power [W]")
        self.ax_pv.grid(True, alpha=0.3)

        self.figure.tight_layout()
        self.canvas.draw()

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.engine.stop()
            self.worker.wait(2000)
        self.b1500.disconnect()
        self.power_meter.disconnect()
        self.stage.disconnect()
        event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = WaferAutomationRolloverGUI()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
