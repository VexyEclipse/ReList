#!/usr/bin/env python3
"""ReList entry point. Launch with python relist.py or run_relist.bat."""
from core import *  # Preserve imports used by older scripts.
from ui import OrganizerApp

if __name__ == "__main__":
    OrganizerApp().mainloop()
