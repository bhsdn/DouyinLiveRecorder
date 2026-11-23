# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from recorder_core.engine import RecorderEngine
from recorder_core.events import Event

from .common import STATE_COLORS, EngineBridge, format_duration, open_path
from .dialogs import BatchImportDialog, RoomDialog

COLUMNS = (
    ('index', '序号'),
    ('name', '主播'),
    ('platform', '平台'),
    ('quality', '画质'),
    ('state_label', '状态'),
    ('duration', '已录时长'),
    ('next_check', '下次检测'),
    ('title', '直播标题'),
    ('url', '直播间地址'),
    ('last_error', '备注'),
)
STATE_FILTERS = (
    ('全部状态', None),
    ('录制中', ('recording',)),
    ('直播中', ('recording', 'live')),
    ('未开播', ('waiting', 'idle', 'checking')),
    ('已暂停', ('paused',)),
    ('出错', ('error',)),
)


class RoomTableModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows: list[dict[str, Any]] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section][1]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        key = COLUMNS[index.column()][0]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if key == 'duration':
                started = row.get('record_started_at')
                return format_duration(time.time() - started) if started else ''
            if key == 'next_check':
                next_check = row.get('next_check')
                return f'{max(0, int(next_check - time.time()))} 秒后' if next_check and row['enabled'] else ''
            if key == 'name':
                return row.get('name') or '(待获取)'
            return row.get(key, '')
        if role == Qt.ItemDataRole.ForegroundRole and key == 'state_label':
            return QColor(STATE_COLORS.get(row['state'], '#202124'))
        if role == Qt.ItemDataRole.ForegroundRole and not row['enabled']:
            return QColor('#9aa0a6')
        if role == Qt.ItemDataRole.UserRole:
            return row
        if role == Qt.ItemDataRole.TextAlignmentRole and key in ('index', 'quality', 'duration'):
            return Qt.AlignmentFlag.AlignCenter
        return None

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def upsert(self, room: dict[str, Any]) -> None:
        for i, row in enumerate(self.rows):
            if row['id'] == room['id']:
                self.rows[i] = room
                self.dataChanged.emit(self.index(i, 0), self.index(i, len(COLUMNS) - 1))
                return

    def remove(self, room_id: str) -> None:
        for i, row in enumerate(self.rows):
            if row['id'] == room_id:
                self.beginRemoveRows(QModelIndex(), i, i)
                self.rows.pop(i)
                self.endRemoveRows()
                return

    def tick(self) -> None:
        if self.rows:
            for column in (5, 6):
                self.dataChanged.emit(self.index(0, column), self.index(len(self.rows) - 1, column),
                                      [Qt.ItemDataRole.DisplayRole])


class RoomFilterModel(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.search = ''
        self.states: tuple[str, ...] | None = None

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        row = self.sourceModel().rows[source_row]
        if self.states and row['state'] not in self.states:
            return False
        if self.search:
            text = ' '.join(str(row.get(k, '')) for k in ('name', 'platform', 'url', 'title')).lower()
            return self.search in text
        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        a, b = left.data(Qt.ItemDataRole.UserRole), right.data(Qt.ItemDataRole.UserRole)
        key = COLUMNS[left.column()][0]
        if key == 'index':
            return a['index'] < b['index']
        if key == 'duration':
            return (a.get('record_started_at') or 0) < (b.get('record_started_at') or 0)
        return str(left.data() or '') < str(right.data() or '')


class RoomsPage(QWidget):
    def __init__(self, engine: RecorderEngine, bridge: EngineBridge):
        super().__init__()
        self.engine = engine
        self.model = RoomTableModel()
        self.proxy = RoomFilterModel()
        self.proxy.setSourceModel(self.model)

        title = QLabel('直播间')
        title.setObjectName('pageTitle')

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText('搜索主播 / 平台 / 地址 / 标题')
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filter_changed)
        self.state_combo = QComboBox()
        for label, _ in STATE_FILTERS:
            self.state_combo.addItem(label)
        self.state_combo.currentIndexChanged.connect(self._filter_changed)

        self.toolbar = QToolBar()
        self.actions: dict[str, QAction] = {}
        for key, text, slot in (
                ('add', '添加', self.add_room),
                ('import', '批量导入', self.import_rooms),
                ('edit', '编辑', self.edit_room),
                ('delete', '删除', self.delete_rooms),
                (None, None, None),
                ('pause', '暂停', self.pause_rooms),
                ('resume', '恢复', self.resume_rooms),
                ('check', '立即检测', self.check_rooms),
                ('stop', '停止本场录制', self.stop_recordings),
                (None, None, None),
                ('folder', '打开目录', self.open_folder),
                ('copy', '复制直播源', self.copy_stream_url),
                (None, None, None),
                ('pause_all', '全部暂停', self.pause_all),
                ('resume_all', '全部恢复', self.resume_all),
        ):
            if key is None:
                self.toolbar.addSeparator()
                continue
            action = QAction(text, self)
            action.triggered.connect(slot)
            self.toolbar.addAction(action)
            self.actions[key] = action
        self.actions['stop'].setToolTip('停止当前录制并保存文件，本场直播不再录制，下次开播时自动恢复')

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for column, width in enumerate((50, 160, 100, 60, 110, 90, 90, 200, 280)):
            self.table.setColumnWidth(column, width)
        self.table.doubleClicked.connect(lambda _: self.edit_room())
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.selectionModel().selectionChanged.connect(self._update_actions)

        filters = QHBoxLayout()
        filters.addWidget(self.search_edit, 1)
        filters.addWidget(self.state_combo)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.toolbar)
        layout.addLayout(filters)
        layout.addWidget(self.table)

        bridge.event.connect(self._on_event)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.model.tick)
        self.timer.start(1000)
        self.refresh()

    # ------------------------------------------------------------------ data

    def refresh(self) -> None:
        self.model.set_rows(self.engine.snapshot())
        self._update_actions()

    def _on_event(self, event: Event) -> None:
        if event.type == 'room_state':
            room = event.data['room']
            if any(r['id'] == room['id'] for r in self.model.rows):
                self.model.upsert(room)
                if self.proxy.states:
                    self.proxy.invalidateFilter()
            self._update_actions()
        elif event.type in ('room_added', 'room_removed'):
            self.refresh()

    def _filter_changed(self) -> None:
        self.proxy.search = self.search_edit.text().strip().lower()
        self.proxy.states = STATE_FILTERS[self.state_combo.currentIndex()][1]
        self.proxy.invalidateFilter()

    def selected_rows(self) -> list[dict[str, Any]]:
        rows = []
        for index in self.table.selectionModel().selectedRows():
            rows.append(self.proxy.data(index, Qt.ItemDataRole.UserRole))
        return rows

    def _update_actions(self) -> None:
        rows = self.selected_rows()
        has, single = bool(rows), len(rows) == 1
        self.actions['edit'].setEnabled(single)
        self.actions['delete'].setEnabled(has)
        self.actions['pause'].setEnabled(any(r['enabled'] for r in rows))
        self.actions['resume'].setEnabled(any(not r['enabled'] for r in rows))
        self.actions['check'].setEnabled(any(r['enabled'] for r in rows))
        self.actions['stop'].setEnabled(any(r['state'] == 'recording' for r in rows))
        self.actions['folder'].setEnabled(single)
        self.actions['copy'].setEnabled(single and bool(rows[0].get('stream_url')))

    def _context_menu(self, pos) -> None:
        self._update_actions()
        menu = QMenu(self)
        for key in ('edit', 'pause', 'resume', 'check', 'stop', 'folder', 'copy', 'delete'):
            if key in ('folder', 'delete'):
                menu.addSeparator()
            menu.addAction(self.actions[key])
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _guard(self, func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (ValueError, KeyError, PermissionError) as e:
            QMessageBox.warning(self, '操作失败', str(e))
            return None

    # ------------------------------------------------------------------ actions

    def add_room(self) -> None:
        dialog = RoomDialog(self, quality=self.engine.settings.quality)
        if dialog.exec():
            v = dialog.values()
            self._guard(self.engine.add_room, v['url'], v['quality'], v['name'], v['enabled'])

    def import_rooms(self) -> None:
        dialog = BatchImportDialog(self, self.engine.settings.quality)
        if not dialog.exec():
            return
        text, quality = dialog.values()
        added, skipped = self.engine.add_rooms_text(text, quality)
        message = f'成功导入 {len(added)} 个直播间。'
        if skipped:
            message += '\n\n以下内容已跳过：\n' + '\n'.join(skipped[:20])
            if len(skipped) > 20:
                message += f'\n... 共 {len(skipped)} 条'
        QMessageBox.information(self, '批量导入', message)

    def edit_room(self) -> None:
        rows = self.selected_rows()
        if len(rows) != 1:
            return
        row = rows[0]
        dialog = RoomDialog(self, row['url'], row['quality'], row['name'], row['enabled'], title='编辑直播间')
        if dialog.exec():
            v = dialog.values()
            self._guard(self.engine.update_room, row['id'], url=v['url'], quality=v['quality'], name=v['name'],
                        enabled=v['enabled'])

    def delete_rooms(self) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        recording = [r for r in rows if r['state'] == 'recording']
        text = f'确定删除选中的 {len(rows)} 个直播间吗？'
        if recording:
            text += f'\n其中 {len(recording)} 个正在录制，将停止录制并保存已录制的文件。'
        if QMessageBox.question(self, '删除直播间', text) == QMessageBox.StandardButton.Yes:
            for row in rows:
                self._guard(self.engine.remove_room, row['id'])

    def pause_rooms(self) -> None:
        for row in self.selected_rows():
            if row['enabled']:
                self._guard(self.engine.pause_room, row['id'])

    def resume_rooms(self) -> None:
        for row in self.selected_rows():
            if not row['enabled']:
                self._guard(self.engine.resume_room, row['id'])

    def check_rooms(self) -> None:
        for row in self.selected_rows():
            self._guard(self.engine.check_now, row['id'])

    def stop_recordings(self) -> None:
        for row in self.selected_rows():
            if row['state'] == 'recording':
                self._guard(self.engine.stop_recording, row['id'])

    def open_folder(self) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        record_file = rows[0].get('record_file')
        folder = os.path.dirname(record_file) if record_file else ''
        if not folder or not os.path.isdir(folder):
            folder = os.path.join(self.engine.save_root, rows[0].get('platform') or '')
        if not os.path.isdir(folder):
            folder = str(self.engine.save_root)
        open_path(folder)

    def copy_stream_url(self) -> None:
        rows = self.selected_rows()
        if rows and rows[0].get('stream_url'):
            QGuiApplication.clipboard().setText(rows[0]['stream_url'])

    def pause_all(self) -> None:
        if QMessageBox.question(self, '全部暂停', '暂停全部直播间？正在录制的会停止并保存。') == \
                QMessageBox.StandardButton.Yes:
            self.engine.pause_all()

    def resume_all(self) -> None:
        self.engine.resume_all()
