# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys

os.environ.setdefault('DLR_SKIP_AUTO_INSTALL', '1')

from PySide6.QtCore import QLockFile, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from recorder_core.engine import RecorderEngine  # noqa: E402
from recorder_core.paths import AppPaths  # noqa: E402

from .common import app_icon  # noqa: E402
from .main_window import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName('DouyinLiveRecorder')
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)

    paths = AppPaths.default()
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(paths.config_dir / '.desktop_gui.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(200):
        QMessageBox.warning(None, 'DouyinLiveRecorder', '程序已经在运行中（请检查系统托盘）。')
        return 1

    engine = RecorderEngine(paths)
    window = MainWindow(engine)
    window.show()
    engine.start()
    QTimer.singleShot(800, window.check_environment)
    code = app.exec()
    lock.unlock()
    return code
