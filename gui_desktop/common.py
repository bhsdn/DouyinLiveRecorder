# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap

from recorder_core.events import Event

STATE_COLORS = {
    'recording': '#d93025',
    'live': '#e37400',
    'checking': '#1a73e8',
    'waiting': '#5f6368',
    'idle': '#5f6368',
    'paused': '#9aa0a6',
    'error': '#b3261e',
    'stopped': '#9aa0a6',
}


class EngineBridge(QObject):
    """Re-emits engine events (raised on worker threads) as a Qt signal delivered on the UI thread."""

    event = Signal(object)

    def __init__(self, bus):
        super().__init__()
        self._unsubscribe = bus.subscribe(self._forward)

    def _forward(self, event: Event) -> None:
        self.event.emit(event)

    def close(self) -> None:
        self._unsubscribe()


class TaskRunner(QObject):
    """Runs a blocking callable on a background thread and reports back on the UI thread."""

    finished = Signal(object, object)

    def __init__(self, parent: QObject, callback: Callable[[Any, Exception | None], None]):
        super().__init__(parent)
        self._callback = callback
        self.finished.connect(self._deliver)

    @Slot(object, object)
    def _deliver(self, result: Any, error: Exception | None) -> None:
        try:
            self._callback(result, error)
        finally:
            self.deleteLater()

    def run(self, func: Callable[[], Any]) -> None:
        def target():
            try:
                self.finished.emit(func(), None)
            except Exception as e:
                self.finished.emit(None, e)

        threading.Thread(target=target, daemon=True).start()


def run_in_background(parent: QObject, func: Callable[[], Any],
                      callback: Callable[[Any, Exception | None], None]) -> None:
    TaskRunner(parent, callback).run(func)


def open_path(path: str | Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def format_size(size: int) -> str:
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} TB'


def format_duration(seconds: int) -> str:
    if not seconds:
        return ''
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f'{h:02d}:{m:02d}:{s:02d}'


def app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor('#d93025'))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
    painter.setBrush(QColor('#ffffff'))
    painter.drawEllipse(22, 22, 20, 20)
    painter.end()
    return QIcon(pixmap)


STYLE_SHEET = """
QWidget { font-size: 13px; }
QListWidget#nav { background: #202124; color: #e8eaed; border: none; padding-top: 8px; outline: 0; }
QListWidget#nav::item { padding: 12px 18px; border-left: 3px solid transparent; }
QListWidget#nav::item:selected { background: #303134; color: #ffffff; border-left: 3px solid #d93025; }
QListWidget#nav::item:hover { background: #2a2b2e; }
QLabel#pageTitle { font-size: 18px; font-weight: bold; padding: 4px 0 8px 0; }
QLabel#hint { color: #5f6368; }
QToolBar { border: none; spacing: 4px; }
QTableView, QTreeWidget { gridline-color: #e0e0e0; selection-background-color: #e8f0fe; selection-color: #202124; }
QHeaderView::section { background: #f1f3f4; padding: 6px; border: none; border-right: 1px solid #e0e0e0; }
QPushButton { padding: 5px 14px; }
QPushButton#primary { background: #1a73e8; color: white; border: none; border-radius: 4px; padding: 6px 18px; }
QPushButton#primary:hover { background: #1765cc; }
QPushButton#danger { color: #b3261e; }
"""
