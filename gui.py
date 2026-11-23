# -*- coding: utf-8 -*-
"""Desktop GUI entry point.  Run:  python gui.py   (requires: pip install -r requirements-gui.txt)"""
import os
import sys

os.environ.setdefault('DLR_SKIP_AUTO_INSTALL', '1')

# Windowed builds (pythonw / PyInstaller --windowed) have no console streams, but src/logger.py
# registers sys.stderr as a log sink at import time.
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')

if __name__ == '__main__':
    from gui_desktop.app import main
    sys.exit(main())
