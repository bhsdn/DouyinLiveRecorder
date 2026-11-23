# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
import html
from collections import deque

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from recorder_core.engine import RecorderEngine
from recorder_core.events import Event

from .common import EngineBridge, open_path

LEVEL_RANK = {'debug': 0, 'info': 1, 'warning': 2, 'error': 3}
LEVEL_COLORS = {'debug': '#80868b', 'info': '#202124', 'warning': '#e37400', 'error': '#d93025'}
FILTERS = (('信息及以上', 1), ('全部（含检测过程）', 0), ('警告及以上', 2), ('仅错误', 3))
MAX_LINES = 5000


class LogsPage(QWidget):
    def __init__(self, engine: RecorderEngine, bridge: EngineBridge):
        super().__init__()
        self.engine = engine
        self.entries: deque[Event] = deque(engine.bus.logs, maxlen=MAX_LINES)

        title = QLabel('运行日志')
        title.setObjectName('pageTitle')
        self.level_combo = QComboBox()
        for label, _ in FILTERS:
            self.level_combo.addItem(label)
        self.level_combo.currentIndexChanged.connect(self._rerender)
        self.follow_check = QCheckBox('自动滚动')
        self.follow_check.setChecked(True)
        clear_button = QPushButton('清空')
        clear_button.clicked.connect(self._clear)
        folder_button = QPushButton('打开日志目录')
        folder_button.clicked.connect(lambda: open_path(self.engine.paths.logs_dir))

        bar = QHBoxLayout()
        bar.addWidget(QLabel('显示级别'))
        bar.addWidget(self.level_combo)
        bar.addWidget(self.follow_check)
        bar.addStretch(1)
        bar.addWidget(folder_button)
        bar.addWidget(clear_button)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(MAX_LINES)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(bar)
        layout.addWidget(self.view, 1)

        bridge.event.connect(self._on_event)
        self._rerender()

    @property
    def min_rank(self) -> int:
        return FILTERS[self.level_combo.currentIndex()][1]

    def _format(self, event: Event) -> str:
        level = event.data['level']
        ts = datetime.datetime.fromtimestamp(event.ts).strftime('%m-%d %H:%M:%S')
        color = LEVEL_COLORS.get(level, '#202124')
        message = html.escape(event.data['message']).replace('\n', '<br>')
        return f'<span style="color:#80868b">{ts}</span> <span style="color:{color}">[{level.upper()}] {message}</span>'

    def _append(self, event: Event) -> None:
        if LEVEL_RANK.get(event.data['level'], 1) < self.min_rank:
            return
        self.view.appendHtml(self._format(event))
        if self.follow_check.isChecked():
            self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def _on_event(self, event: Event) -> None:
        if event.type == 'log':
            self.entries.append(event)
            self._append(event)

    def _rerender(self) -> None:
        self.view.clear()
        for event in self.entries:
            self._append(event)

    def _clear(self) -> None:
        self.entries.clear()
        self.view.clear()
