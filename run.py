"""
run.py
======
Entry point for the VCSEL Wafer Automation GUI.

Usage
-----
::

    python run.py

Or, after ``pip install -e .``::

    wafer-automation
"""

from wafer_automation.gui.main_window import main

if __name__ == "__main__":
    main()
