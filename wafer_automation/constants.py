"""
constants.py
============
All Cascade / Nucleus stage protocol constants and GUI option lists.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Cascade Nucleus device-ID injection
# ---------------------------------------------------------------------------

CASCADE_DEFAULT_DEVICE_ID = "2"
CASCADE_KNOWN_DEVICE_IDS = {"1", "2"}

# Command prefixes that require a device-ID token as the second word.
CASCADE_DEVICE_ID_COMMAND_PREFIXES = (
    ":mov:up",
    ":mov:down",
    ":mov:abs",
    ":move:abs",
    ":move:rel",
    ":mov:prob:",
    ":move:prob:",
    ":set:prob:",
)

# ---------------------------------------------------------------------------
# Commonly used Cascade Nucleus commands
# ---------------------------------------------------------------------------

CASCADE_POSITION_QUERY_COMMAND        = ":move:abs?"
CASCADE_FIRST_DIE_COMMAND             = ":mov:prob:firs:die"
CASCADE_NEXT_DIE_COMMAND              = ":mov:prob:next:die"
CASCADE_CURRENT_DIE_QUERY_COMMAND     = ":mov:prob:abs:die?"
CASCADE_CURRENT_SUBSITE_QUERY_COMMAND = ":mov:prob:abs:subs?"

# ---------------------------------------------------------------------------
# Nucleus command option lists (used to populate UI combo-boxes)
# ---------------------------------------------------------------------------

NUCLEUS_TIP_UP_OPTIONS = [
    ":mov:down",
    ":mov:up",
    ":mov:down 2",
    ":mov:up 2",
    "",
]

NUCLEUS_TIP_DOWN_OPTIONS = [
    ":mov:up",
    ":mov:down",
    ":mov:up 2",
    ":mov:down 2",
    "",
]

NUCLEUS_MOVE_OPTIONS = [
    ":move:rel {dx_um} {dy_um} 0",
    ":move:rel {dx_mm} {dy_mm} 0",
    ":move:rel 2 {dx_um} {dy_um} 0",
    ":move:rel 2 {dx_mm} {dy_mm} 0",
    "",
]

NUCLEUS_NEXT_SITE_OPTIONS = [
    ":mov:prob:next:die",
    ":mov:prob:next:subs",
    ":mov:prob:next:die 2",
    ":mov:prob:next:subs 2",
    "",
]

NUCLEUS_HOME_OPTIONS = [
    ":mov:prob:firs:die",
    ":mov:prob:firs:die; :mov:prob:firs:subs",
    ":mov:prob:firs:die 2",
    ":mov:prob:firs:die 2; :mov:prob:firs:subs 2",
    "",
]

# Commands that register the current physical stage position as die (1, 1).
NUCLEUS_SET_REF_DIE_OPTIONS = [
    ":set:prob:firs:die",
    ":set:prob:firs:die 2",
    ":set:prob:firs:die; :set:prob:firs:subs",
    ":set:prob:firs:die 2; :set:prob:firs:subs 2",
    "",
]

# ---------------------------------------------------------------------------
# Wafer scan-order identifiers
# ---------------------------------------------------------------------------

SCAN_MODE_ROW_LTR = "row_ltr_reset"   # left-to-right in each row
SCAN_MODE_COL_TTB = "col_ttb_reset"   # top-to-bottom in each column

# ---------------------------------------------------------------------------
# GUI option lists
# ---------------------------------------------------------------------------

SPECTROMETER_HOOK_ACTIONS = [
    "Disabled",
    "Write hook config only",
    "Write config and launch spectrometer GUI",
]

ROLLOVER_METHODS = ["cusum", "ewma", "rolling_avg", "regression"]
