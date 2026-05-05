"""
models.py
=========
Plain data-classes shared across the automation engine and GUI.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .constants import SCAN_MODE_ROW_LTR


@dataclass
class SpectrometerHookConfig:
    """Optional post-measurement hook that writes a JSON config for an
    external spectrometer script."""

    action: str = "Disabled"
    integration_time_ms: float = 100.0
    averages: int = 1
    use_hardware_sync: bool = True
    timed_bias_during_spectrum: bool = True
    timed_bias_interval_ms: float = 10.0


@dataclass
class StageConfig:
    """All parameters that govern how the Cascade chuck moves between sites."""

    transport: str = "Simulation"
    address: str = ""
    timeout_ms: int = 5000
    # Chuck direction semantics (Nucleus firmware):
    #   ":mov:down"  = separate tips (chuck moves down)
    #   ":mov:up"    = contact tips  (chuck moves up)
    lift_command: str = ":mov:down"
    lower_command: str = ":mov:up"
    move_command_template: str = ":move:rel {dx_um} {dy_um} 0"
    next_site_command: str = ":mov:prob:next:die"
    home_command: str = ":mov:prob:firs:die"
    settle_after_up_s: float = 0.5
    settle_after_move_s: float = 1.0
    settle_after_down_s: float = 0.5


@dataclass
class AutomationConfig:
    """Parameters that define the wafer map and automation run."""

    total_sites: int = 5
    start_site: int = 1
    map_rows: int = 5
    map_cols: int = 5
    scan_mode: str = SCAN_MODE_ROW_LTR
    x_step_um: float = 100.0
    y_step_um: float = 0.0
    use_relative_move: bool = True
    output_folder: str = "wafer_automation_results"
    device_name: str = "WaferDevice"


@dataclass
class SiteSummary:
    """Result record for a single measured site."""

    site_number: int
    row_number: int
    column_number: int
    points: int
    max_current_a: float
    max_power_w: float
    csv_path: str
    hook_path: str = ""
    status: str = "OK"
    # Rollover-specific fields
    rollover_detected: bool = False
    stop_reason: str = "sweep_complete"
    peak_power_w: float = 0.0
    peak_voltage_v: float = 0.0
    peak_current_a: float = 0.0
    peak_point_index: int = -1
