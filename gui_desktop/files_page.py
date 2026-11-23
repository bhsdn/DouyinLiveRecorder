# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from recorder_core.engine import RecorderEngine
from recorder_core.events import Event

from .common import EngineBridge, format_size, open_path, run_in_background

CONVERTIBLE = ('.ts', '.flv', '.mkv')


class FilesPage(QWidget):
    def __init__(self, engine: RecorderEngine, bridge: EngineBridge):
        super().__init__()
        self.engine = engine
        self._items: list[dict] = []

        title = QLabel('录制文件')
        title.setObjectName('pageTitle')
        self.root_label = QLabel()
        self.root_label.setObjectName('hint')

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText('搜索文件名或目录')
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._render)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['文件名', '目录', '大小', '修改时间'])
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(1, 260)
        self.tree.setColumnWidth(2, 90)
        self.tree.setColumnWidth(3, 150)
        self.tree.itemDoubleClicked.connect(lambda item, _: open_path(item.data(0, Qt.ItemDataRole.UserRole)))
        self.tree.itemSelectionChanged.connect(self._update_buttons)

        self.buttons = {}
        row = QHBoxLayout()
        for key, text, slot in (
                ('refresh', '刷新', self.refresh),
                ('root', '打开录制目录', lambda: open_path(self.engine.save_root)),
                ('open', '打开', self.open_selected),
                ('folder', '打开所在文件夹', self.open_folder),
                ('convert', '转为 MP4', self.convert_selected),
                ('delete', '删除', self.delete_selected),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
            self.buttons[key] = button
        self.buttons['delete'].setObjectName('danger')
        row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.root_label)
        layout.addLayout(row)
        layout.addWidget(self.search_edit)
        layout.addWidget(self.tree, 1)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh)
        bridge.event.connect(self._on_event)
        self._update_buttons()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def _on_event(self, event: Event) -> None:
        if event.type in ('recording_finished', 'file_converted', 'recording_started') and self.isVisible():
            self._refresh_timer.start(1500)

    def refresh(self) -> None:
        self.root_label.setText(f'录制目录：{self.engine.save_root}')

        def done(result, error):
            if error:
                QMessageBox.warning(self, '读取失败', str(error))
                return
            self._items = result
            self._render()

        run_in_background(self, self.engine.list_recordings, done)

    def _render(self) -> None:
        text = self.search_edit.text().strip().lower()
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for item in self._items:
            if text and text not in item['relative'].lower():
                continue
            folder = os.path.dirname(item['relative'])
            mtime = datetime.datetime.fromtimestamp(item['mtime']).strftime('%Y-%m-%d %H:%M:%S')
            node = QTreeWidgetItem([item['name'], folder, format_size(item['size']), mtime])
            node.setData(0, Qt.ItemDataRole.UserRole, item['path'])
            node.setData(2, Qt.ItemDataRole.UserRole, item['size'])
            node.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tree.addTopLevelItem(node)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(3, Qt.SortOrder.DescendingOrder)
        self._update_buttons()

    def _selected_paths(self) -> list[str]:
        return [item.data(0, Qt.ItemDataRole.UserRole) for item in self.tree.selectedItems()]

    def _update_buttons(self) -> None:
        paths = self._selected_paths()
        self.buttons['open'].setEnabled(len(paths) == 1)
        self.buttons['folder'].setEnabled(len(paths) == 1)
        self.buttons['convert'].setEnabled(any(p.lower().endswith(CONVERTIBLE) for p in paths))
        self.buttons['delete'].setEnabled(bool(paths))

    def open_selected(self) -> None:
        for path in self._selected_paths()[:1]:
            open_path(path)

    def open_folder(self) -> None:
        for path in self._selected_paths()[:1]:
            open_path(os.path.dirname(path))

    def convert_selected(self) -> None:
        for path in self._selected_paths():
            if path.lower().endswith(CONVERTIBLE):
                try:
                    self.engine.convert_recording(path)
                except (PermissionError, FileNotFoundError) as e:
                    QMessageBox.warning(self, '转换失败', str(e))

    def delete_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        if QMessageBox.question(self, '删除文件', f'确定永久删除选中的 {len(paths)} 个文件吗？') != \
                QMessageBox.StandardButton.Yes:
            return
        errors = []
        for path in paths:
            try:
                self.engine.delete_recording(path)
            except (PermissionError, FileNotFoundError, OSError) as e:
                errors.append(f'{os.path.basename(path)}: {e}')
        if errors:
            QMessageBox.warning(self, '部分文件未删除', '\n'.join(errors))
        self.refresh()
