#!/usr/bin/env python3
"""
Game Stick Lite Studio — Custom Firmware Builder & Multi-Tool
Main application entry point.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stick_studio.ui.main_window import run_app

if __name__ == "__main__":
    run_app()
