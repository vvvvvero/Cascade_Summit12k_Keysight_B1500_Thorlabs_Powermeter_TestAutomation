"""
tests/test_engine.py
====================
Unit tests for WaferAutomationEngine that run without any hardware.

All B1500 / power-meter / stage objects are replaced by minimal stubs so no
VISA driver or physical instruments are needed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

from wafer_automation.constants import SCAN_MODE_COL_TTB, SCAN_MODE_ROW_LTR
from wafer_automation.engine import WaferAutomationEngine
from wafer_automation.models import (
    AutomationConfig,
    SiteSummary,
    SpectrometerHookConfig,
    StageConfig,
)


# ---------------------------------------------------------------------------
# Minimal stubs (no real hardware, no pyvisa)
# ---------------------------------------------------------------------------

class _FakeB1500:
    connected = False
    idn = "Simulated B1500"

    def connect(self, resource):
        self.connected = True
        return True, "Connected (sim)"

    def disconnect(self):
        self.connected = False


class _FakePowerMeter:
    connected = False
    idn = "Simulated PM"

    def connect(self, resource):
        self.connected = True
        return True, "Connected (sim)"

    def disconnect(self):
        self.connected = False


class _FakeStage:
    """Stage stub that records every command sent to it."""

    connected = True
    idn = "Simulated Stage"
    _commands: List[str]

    def __init__(self):
        self._commands = []

    def lift(self, command: str) -> str:
        self._commands.append(("lift", command))
        return "OK"

    def lower(self, command: str) -> str:
        self._commands.append(("lower", command))
        return "OK"

    def home(self, command: str) -> str:
        self._commands.append(("home", command))
        return "OK"

    def move_next(self, *, dx_um, dy_um, use_relative_move,
                  move_template, next_site_command, site) -> str:
        self._commands.append(("move", dx_um, dy_um))
        return "OK"

    def send_command(self, command: str) -> str:
        self._commands.append(("raw", command))
        return "OK"

    def preview_commands(self, command: str) -> List[str]:
        return [command]


class _FakeSweepResult:
    """Mimics the return value of SynchronizedMeasurementEngine.run()."""

    def __init__(self, n_points: int = 3):
        self.points = [
            MagicMock(
                point_index=i,
                voltage=float(i) * 0.5,
                current=float(i) * 0.01,
                optical_power=float(i) * 0.001,
                status="normal",
                timestamp=time.time(),
                relative_time=float(i) * 0.1,
                setpoint=float(i) * 0.5,
            )
            for i in range(n_points)
        ]
        self.rollover = MagicMock(
            detected=False,
            stop_reason="sweep_complete",
            peak_power=0.002,
            peak_voltage=1.0,
            peak_current=0.02,
            peak_point_index=2,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(
    total_sites: int = 3,
    rows: int = 3,
    cols: int = 3,
    scan_mode: str = SCAN_MODE_ROW_LTR,
    x_step_um: float = 100.0,
    y_step_um: float = 100.0,
) -> Tuple[WaferAutomationEngine, _FakeStage]:
    from b1500_powermeter_rollover import SweepConfig

    b1500 = _FakeB1500()
    pm = _FakePowerMeter()
    stage = _FakeStage()

    sweep_cfg = MagicMock(spec=SweepConfig)
    sweep_cfg.enable_rollover = False
    sweep_cfg.start = 0.0
    sweep_cfg.stop = 1.0
    sweep_cfg.steps = 3
    sweep_cfg.dwell_s = 0.0
    sweep_cfg.smu = 1
    sweep_cfg.mode = "iv"
    sweep_cfg.compliance = 0.1
    sweep_cfg.two_direction = False
    sweep_cfg.enable_power_meter = False
    sweep_cfg.power_wavelength_nm = 850.0
    sweep_cfg.power_averages = 1
    sweep_cfg.autosave = False
    sweep_cfg.output_folder = ""
    sweep_cfg.device_name = "VCSEL"

    stage_cfg = StageConfig(
        transport="Simulation",
        address="",
        lift_command=":mov:down",
        lower_command=":mov:up",
        move_command_template=":move:rel {dx_um} {dy_um} 0",
        next_site_command=":mov:prob:next:die",
        home_command=":mov:prob:firs:die",
        settle_after_up_s=0.0,
        settle_after_move_s=0.0,
        settle_after_down_s=0.0,
    )

    auto_cfg = AutomationConfig(
        total_sites=total_sites,
        start_site=1,
        map_rows=rows,
        map_cols=cols,
        scan_mode=scan_mode,
        x_step_um=x_step_um,
        y_step_um=y_step_um,
        use_relative_move=True,
        output_folder="",
        device_name="VCSEL",
    )

    spec_cfg = SpectrometerHookConfig(
        action="none",
        integration_time_ms=100.0,
        averages=1,
        use_hardware_sync=False,
        timed_bias_during_spectrum=False,
        timed_bias_interval_ms=10.0,
    )

    engine = WaferAutomationEngine(
        b1500, pm, stage, sweep_cfg, stage_cfg, auto_cfg, spec_cfg
    )
    return engine, stage


# ---------------------------------------------------------------------------
# Tests: _site_row_col
# ---------------------------------------------------------------------------

class TestSiteRowCol:
    """Verify the (row, col) mapping for different scan modes."""

    def test_row_ltr_linear(self):
        engine, _ = _make_engine(rows=2, cols=3, scan_mode=SCAN_MODE_ROW_LTR)
        expected = [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)]
        actual = [engine._site_row_col(i) for i in range(6)]
        assert actual == expected

    def test_col_ttb_linear(self):
        engine, _ = _make_engine(rows=2, cols=3, scan_mode=SCAN_MODE_COL_TTB)
        # 3 columns × 2 rows → col-major order
        expected = [(1, 1), (2, 1), (1, 2), (2, 2), (1, 3), (2, 3)]
        actual = [engine._site_row_col(i) for i in range(6)]
        assert actual == expected

    def test_single_row(self):
        engine, _ = _make_engine(rows=1, cols=5, scan_mode=SCAN_MODE_ROW_LTR)
        expected = [(1, c) for c in range(1, 6)]
        actual = [engine._site_row_col(i) for i in range(5)]
        assert actual == expected

    def test_single_col(self):
        engine, _ = _make_engine(rows=4, cols=1, scan_mode=SCAN_MODE_ROW_LTR)
        expected = [(r, 1) for r in range(1, 5)]
        actual = [engine._site_row_col(i) for i in range(4)]
        assert actual == expected


# ---------------------------------------------------------------------------
# Tests: _compute_relative_move_sequence
# ---------------------------------------------------------------------------

class TestRelativeMoveSequence:
    """Verify the relative-move geometry is correct."""

    def test_same_row_move_one_column(self):
        engine, _ = _make_engine(
            rows=2, cols=3, scan_mode=SCAN_MODE_ROW_LTR,
            x_step_um=200.0, y_step_um=150.0
        )
        # site 0 → site 1 = col 1→2 in row 1 (same row, +1 col)
        moves = engine._compute_relative_move_sequence(0)
        assert len(moves) == 1
        _label, dx, dy = moves[0]
        assert dx == pytest.approx(200.0)
        assert dy == pytest.approx(0.0)

    def test_row_wrap_returns_to_col1_then_advances_row(self):
        engine, _ = _make_engine(
            rows=2, cols=2, scan_mode=SCAN_MODE_ROW_LTR,
            x_step_um=100.0, y_step_um=80.0,
        )
        # sequence 1 is row=1, col=2  →  sequence 2 is row=2, col=1
        moves = engine._compute_relative_move_sequence(1)
        # First move: return to column 1 (-1 column step X)
        # Second move: advance to row 2 (+1 row step Y)
        assert len(moves) == 2
        _l1, dx1, dy1 = moves[0]
        _l2, dx2, dy2 = moves[1]
        assert dx1 == pytest.approx(-100.0)
        assert dy1 == pytest.approx(0.0)
        assert dx2 == pytest.approx(0.0)
        assert dy2 == pytest.approx(80.0)

    def test_col_ttb_same_column_advance(self):
        engine, _ = _make_engine(
            rows=3, cols=2, scan_mode=SCAN_MODE_COL_TTB,
            x_step_um=100.0, y_step_um=75.0,
        )
        # sequence 0 is row=1, col=1  →  sequence 1 is row=2, col=1 (same col)
        moves = engine._compute_relative_move_sequence(0)
        assert len(moves) == 1
        _l, dx, dy = moves[0]
        assert dx == pytest.approx(0.0)
        # dy should move downward (negative because row increases → Y decreases)
        assert dy == pytest.approx(-75.0)

    def test_col_wrap_returns_to_row1_then_advances_col(self):
        engine, _ = _make_engine(
            rows=2, cols=3, scan_mode=SCAN_MODE_COL_TTB,
            x_step_um=100.0, y_step_um=75.0,
        )
        # sequence 1 is row=2, col=1  →  sequence 2 is row=1, col=2 (col wrap)
        moves = engine._compute_relative_move_sequence(1)
        assert len(moves) == 2
        _l1, dx1, dy1 = moves[0]   # return to row 1
        _l2, dx2, dy2 = moves[1]   # advance one column
        assert dy1 == pytest.approx(75.0)   # back to row=1 (row_index=0)
        assert dx1 == pytest.approx(0.0)
        assert dx2 == pytest.approx(100.0)
        assert dy2 == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Tests: engine.run() stage sequence (no real sweep)
# ---------------------------------------------------------------------------

class TestEngineRunSequence:
    """Verify the lift/move/lower sequence without hardware."""

    @pytest.fixture()
    def engine_and_stage(self, tmp_path):
        engine, stage = _make_engine(
            total_sites=3, rows=1, cols=3, scan_mode=SCAN_MODE_ROW_LTR,
        )
        engine.automation_config = engine.automation_config.__class__(
            **{
                **engine.automation_config.__dict__,
                "output_folder": str(tmp_path),
                "total_sites": 3,
            }
        )
        return engine, stage

    def test_run_logs_collected(self, engine_and_stage):
        engine, stage = engine_and_stage
        logs = []
        engine.on_log = lambda msg: logs.append(msg)

        fake_result = _FakeSweepResult()

        with (
            patch(
                "wafer_automation.engine.SynchronizedMeasurementEngine"
            ) as MockSME,
        ):
            mock_instance = MagicMock()
            mock_instance.run.return_value = fake_result.points, fake_result.rollover
            MockSME.return_value = mock_instance
            engine.run()

        assert any("site" in log.lower() or "site" in log.lower() for log in logs), \
            "Expected at least one site-related log message"

    def test_initial_lower_before_first_site(self, engine_and_stage):
        engine, stage = engine_and_stage
        fake_result = _FakeSweepResult()

        with patch("wafer_automation.engine.SynchronizedMeasurementEngine") as MockSME:
            mock_instance = MagicMock()
            mock_instance.run.return_value = fake_result.points, fake_result.rollover
            MockSME.return_value = mock_instance
            engine.run()

        # The very first stage operation must be a 'lower' (tips down onto site 1)
        assert stage._commands, "No stage commands recorded"
        first_op = stage._commands[0]
        assert first_op[0] == "lower", (
            f"Expected first stage op to be 'lower', got {first_op[0]!r}. "
            f"Full sequence: {stage._commands}"
        )

    def test_lift_between_sites(self, engine_and_stage):
        engine, stage = engine_and_stage
        fake_result = _FakeSweepResult()

        with patch("wafer_automation.engine.SynchronizedMeasurementEngine") as MockSME:
            mock_instance = MagicMock()
            mock_instance.run.return_value = fake_result.points, fake_result.rollover
            MockSME.return_value = mock_instance
            engine.run()

        ops = [c[0] for c in stage._commands]
        # Between site 1→2 and 2→3 we need lift, move, lower
        assert "lift" in ops
        assert "move" in ops
        assert "lower" in ops

    def test_final_lift_after_last_site(self, engine_and_stage):
        engine, stage = engine_and_stage
        fake_result = _FakeSweepResult()

        with patch("wafer_automation.engine.SynchronizedMeasurementEngine") as MockSME:
            mock_instance = MagicMock()
            mock_instance.run.return_value = fake_result.points, fake_result.rollover
            MockSME.return_value = mock_instance
            engine.run()

        # The last stage operation must be a 'lift'
        ops = [c[0] for c in stage._commands]
        assert ops[-1] == "lift", (
            f"Expected last stage op to be 'lift', got {ops[-1]!r}. "
            f"Full op sequence: {ops}"
        )

    def test_site_summaries_count(self, engine_and_stage):
        engine, stage = engine_and_stage
        fake_result = _FakeSweepResult()

        with patch("wafer_automation.engine.SynchronizedMeasurementEngine") as MockSME:
            mock_instance = MagicMock()
            mock_instance.run.return_value = fake_result.points, fake_result.rollover
            MockSME.return_value = mock_instance
            summaries = engine.run()

        assert len(summaries) == 3, (
            f"Expected 3 site summaries, got {len(summaries)}"
        )

    def test_stop_requested_exits_early(self, engine_and_stage):
        engine, stage = engine_and_stage
        fake_result = _FakeSweepResult()

        call_count = 0

        def fake_run():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                engine.stop()
            return fake_result.points, fake_result.rollover

        with patch("wafer_automation.engine.SynchronizedMeasurementEngine") as MockSME:
            mock_instance = MagicMock()
            mock_instance.run.side_effect = fake_run
            MockSME.return_value = mock_instance
            summaries = engine.run()

        assert len(summaries) < 3, (
            "Expected early stop to produce fewer than 3 site summaries"
        )
