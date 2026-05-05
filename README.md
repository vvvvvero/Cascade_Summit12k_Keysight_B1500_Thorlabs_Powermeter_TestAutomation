# VCSEL Wafer Automation

Automated wafer-level LIV (Light-Current-Voltage) measurement with per-site
**optical rollover detection** for VCSEL arrays.

Built on top of the
[`b1500_powermeter_rollover`](https://github.com/vvvvvero/b1500_powermeter_LIV_rollover)
library, which provides the sweep engine, rollover algorithms, and instrument
drivers.

© Veronica Gao ZHan – May 2026

---

## Hardware requirements

| Component | Model |
|-----------|-------|
| Semiconductor parameter analyser | Keysight B1500A |
| Optical power meter | Thorlabs PM100D (USB-HID VISA) |
| Probe station / stage controller | Cascade Summit 12000 AP (Nucleus TCP / GPIB) |

---

## Software requirements

- Python ≥ 3.8 (tested on 3.11)
- Windows 10/11 (NI-VISA required for B1500 GPIB)
- NI-VISA ≥ 21.x **or** `pyvisa-py` + `pyserial` (for serial-only setups)

---

## Installation

```bash
# 1. Clone this repository
git clone https://github.com/vvvvvero/Cascade_Summit12k_Keysight_B1500_Thorlabs_Powermeter_TestAutomation
cd vcsel_wafer_automation

# 2. Create and activate a virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate

# 3. Install the b1500_powermeter_rollover dependency
#    (from a local checkout next to this folder)
pip install -e ../b1500_powermeter_rollover

# 4. Install this package in editable mode
pip install -e .
```

---

## Usage

```bash
# Run with the console entry-point (after pip install)
wafer-automation

# Or directly from the project folder
python run.py
```

---

## GUI overview

The window is divided into six configuration sections:

| Section | Description |
|---------|-------------|
| **1. Device Connections** | Connect B1500 (GPIB), Thorlabs power meter (USB), Cascade stage (TCP / VISA / Simulation) |
| **2. Measurement Settings** | Sweep mode (IV / VI), voltage/current range, dwell time, compliance |
| **3. Rollover Detection** | Algorithm (cusum / ewma / rolling_avg / regression), threshold, window, CUSUM parameters |
| **4. Wafer Step Automation** | Number of sites, grid shape (rows × columns), step size (µm), settle times |
| **5. Stage / Nucleus Commands** | Tip-up / tip-down / move / home commands; test buttons; position query |
| **6. Output / Spectrometer Hook** | Output folder, device name, optional spectrometer synchronisation settings |

---

## Measurement sequence

```
lower tips onto site 1
│
├── sweep site 1  ──► rollover? ──► stop sweep early if detected
│
├── lift tips
├── move stage (X/Y)
├── lower tips onto site 2
│
├── sweep site 2
│   ...
│
└── lift tips (end of run)
```

Each site produces:
- An individual CSV (`<device>_site<N>_<timestamp>.csv`) with columns
  `voltage_V`, `current_A`, `power_W`
- A summary row in `summary.csv` with peak current, peak power,
  rollover flag, and stop reason

---

## Rollover detection algorithms

| Method | Description | Best for |
|--------|-------------|---------|
| `cusum` | CUSUM (cumulative-sum) control chart — fastest reaction | Production runs — 1–2 point reaction time |
| `ewma` | Exponentially-weighted moving average | Noisy signals |
| `rolling_avg` | Simple rolling window average | Easy to interpret |
| `regression` | Linear regression slope over window | Trend detection |

All algorithms compare against a configurable **threshold** (% of peak power
observed so far) and stop the sweep when the signal drops below that level.

---

## Project structure

```
vcsel_wafer_automation/
├── run.py                          # entry point
├── pyproject.toml
├── README.md
├── .gitignore
├── wafer_automation/
│   ├── __init__.py
│   ├── constants.py                # Cascade / Nucleus protocol constants
│   ├── models.py                   # StageConfig, AutomationConfig, SiteSummary, …
│   ├── stage_controller.py         # CascadeNucleusController
│   ├── engine.py                   # WaferAutomationEngine (GUI-agnostic loop)
│   └── gui/
│       ├── __init__.py
│       ├── main_window.py          # WaferAutomationRolloverGUI + main()
│       └── workers.py              # AutomationWorker, _ResourceScanWorker
└── tests/
    ├── __init__.py
    └── test_engine.py
```

---

## Running the tests

```bash
pip install -e ".[dev]"
pytest tests/
```

---

## License

© Veronica Gao ZHan

---

## Acknowledgements

This project was developed using vibe coding — an AI-assisted development workflow powered by GitHub Copilot. The architecture, code structure, and implementation were generated through iterative natural-language prompting and human review.