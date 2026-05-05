"""
wafer_automation
================
Cascade Summit + Keysight B1500 wafer-level LIV measurement package
with per-site rollover detection.

Public API
----------
.. code-block:: python

    from wafer_automation import (
        AutomationConfig,
        CascadeNucleusController,
        SiteSummary,
        SpectrometerHookConfig,
        StageConfig,
        WaferAutomationEngine,
    )
"""

from .constants import (  # noqa: F401 — re-exported for convenience
    CASCADE_CURRENT_DIE_QUERY_COMMAND,
    CASCADE_CURRENT_SUBSITE_QUERY_COMMAND,
    CASCADE_DEFAULT_DEVICE_ID,
    CASCADE_FIRST_DIE_COMMAND,
    CASCADE_NEXT_DIE_COMMAND,
    CASCADE_POSITION_QUERY_COMMAND,
    ROLLOVER_METHODS,
    SCAN_MODE_COL_TTB,
    SCAN_MODE_ROW_LTR,
    SPECTROMETER_HOOK_ACTIONS,
)
from .engine import WaferAutomationEngine  # noqa: F401
from .models import (  # noqa: F401
    AutomationConfig,
    SiteSummary,
    SpectrometerHookConfig,
    StageConfig,
)
from .stage_controller import CascadeNucleusController  # noqa: F401

__all__ = [
    # constants
    "CASCADE_CURRENT_DIE_QUERY_COMMAND",
    "CASCADE_CURRENT_SUBSITE_QUERY_COMMAND",
    "CASCADE_DEFAULT_DEVICE_ID",
    "CASCADE_FIRST_DIE_COMMAND",
    "CASCADE_NEXT_DIE_COMMAND",
    "CASCADE_POSITION_QUERY_COMMAND",
    "ROLLOVER_METHODS",
    "SCAN_MODE_COL_TTB",
    "SCAN_MODE_ROW_LTR",
    "SPECTROMETER_HOOK_ACTIONS",
    # models
    "AutomationConfig",
    "SiteSummary",
    "SpectrometerHookConfig",
    "StageConfig",
    # engine & stage
    "CascadeNucleusController",
    "WaferAutomationEngine",
]
