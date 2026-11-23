# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from recorder_core.engine import RecorderEngine
from recorder_core.events import Event
from recorder_core.settings import (
    COOKIE_SPECS,
    GROUP_ACCOUNT,
    GROUP_FILES,
    GROUP_PROXY,
    GROUP_PUSH,
    GROUP_RECORD,
    GROUP_UI,
    GROUP_WEB,
    SPECS,
    FieldSpec,
    Settings,
    split_list,
)

from .common import EngineBridge, open_path, run_in_background

GROUP_ORDER = (GROUP_RECORD, GROUP_FILES, GROUP_PROXY, GROUP_PUSH, GROUP_ACCOUNT, 'Cookie', GROUP_WEB, GROUP_UI)


class FieldEditor:
    """Wraps the widget for one FieldSpec with uniform get/set."""

    def __init__(self, spec: FieldSpec, on_change: Callable[[], None]):
        self.spec = spec
        kind = spec.kind
        if kind == 'bool':
            self.widget = QCheckBox(spec.label)
            self.widget.toggled.connect(on_change)
        elif kind == 'int':
            self.widget = QSpinBox()
            self.widget.setRange(0, 10_000_000)
            self.widget.valueChanged.connect(on_change)
        elif kind == 'float':
            self.widget = QDoubleSpinBox()
            self.widget.setRange(0, 100_000)
            self.widget.setDecimals(2)
            self.widget.valueChanged.connect(on_change)
        elif kind == 'choice':
            self.widget = QComboBox()
            self.widget.addItems(spec.choices)
            self.widget.currentTextChanged.connect(on_change)
        elif kind == 'multi':
            self.widget = QWidget()
            row = QHBoxLayout(self.widget)
            row.setContentsMargins(0, 0, 0, 0)
            self.boxes = {}
            for choice in spec.choices:
                box = QCheckBox(choice)
                box.toggled.connect(on_change)
                row.addWidget(box)
                self.boxes[choice] = box
            row.addStretch(1)
        elif kind == 'path':
            self.widget = QWidget()
            row = QHBoxLayout(self.widget)
            row.setContentsMargins(0, 0, 0, 0)
            self.line = QLineEdit()
            self.line.textChanged.connect(on_change)
            browse = QPushButton('浏览…')
            browse.clicked.connect(self._browse)
            row.addWidget(self.line, 1)
            row.addWidget(browse)
        else:
            self.widget = QLineEdit()
            if kind == 'password':
                self.widget.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
            self.widget.textChanged.connect(on_change)
        if spec.help:
            self.widget.setToolTip(spec.help)
            if isinstance(self.widget, QLineEdit):
                self.widget.setPlaceholderText(spec.help)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self.widget, '选择保存目录', self.line.text())
        if folder:
            self.line.setText(folder)

    def set(self, value: Any) -> None:
        kind, w = self.spec.kind, self.widget
        if kind == 'bool':
            w.setChecked(bool(value))
        elif kind in ('int', 'float'):
            w.setValue(value)
        elif kind == 'choice':
            if w.findText(str(value)) < 0:
                w.addItem(str(value))
            w.setCurrentText(str(value))
        elif kind == 'multi':
            chosen = {item.upper() for item in split_list(str(value))}
            for choice, box in self.boxes.items():
                box.setChecked(choice.upper() in chosen or choice in str(value))
        elif kind == 'path':
            self.line.setText(str(value or ''))
        else:
            w.setText(str(value or ''))

    def get(self) -> Any:
        kind, w = self.spec.kind, self.widget
        if kind == 'bool':
            return w.isChecked()
        if kind in ('int', 'float'):
            return w.value()
        if kind == 'choice':
            return w.currentText()
        if kind == 'multi':
            return ', '.join(choice for choice, box in self.boxes.items() if box.isChecked())
        if kind == 'path':
            return self.line.text().strip()
        return w.text().strip()


class SettingsPage(QWidget):
    def __init__(self, engine: RecorderEngine, bridge: EngineBridge):
        super().__init__()
        self.engine = engine
        self.editors: dict[str, FieldEditor] = {}
        self.cookie_edits: dict[str, QLineEdit] = {}
        self.cookie_rows: dict[str, tuple[QLabel, QLineEdit]] = {}
        self._loading = False
        self._dirty = False

        title = QLabel('设置')
        title.setObjectName('pageTitle')
        self.tabs = QTabWidget()
        groups: dict[str, list[FieldSpec]] = {}
        for spec in SPECS:
            groups.setdefault(spec.group, []).append(spec)
        for group in GROUP_ORDER:
            if group == 'Cookie':
                self.tabs.addTab(self._build_cookie_tab(), 'Cookie')
            elif group in groups:
                self.tabs.addTab(self._build_group_tab(groups[group]), group)

        self.status_label = QLabel()
        self.status_label.setObjectName('hint')
        save_button = QPushButton('保存')
        save_button.setObjectName('primary')
        save_button.clicked.connect(self.save)
        revert_button = QPushButton('还原')
        revert_button.clicked.connect(lambda: self.load(self.engine.settings))
        test_button = QPushButton('发送测试推送')
        test_button.clicked.connect(self.test_push)
        folder_button = QPushButton('打开配置文件夹')
        folder_button.clicked.connect(lambda: open_path(self.engine.paths.config_dir))

        buttons = QHBoxLayout()
        buttons.addWidget(self.status_label, 1)
        buttons.addWidget(folder_button)
        buttons.addWidget(test_button)
        buttons.addWidget(revert_button)
        buttons.addWidget(save_button)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(buttons)

        bridge.event.connect(self._on_event)
        self.load(engine.settings)

    def _scroll(self, inner: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(inner)
        return area

    def _build_group_tab(self, specs: list[FieldSpec]) -> QWidget:
        inner = QWidget()
        form = QFormLayout(inner)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for spec in specs:
            editor = FieldEditor(spec, self._mark_dirty)
            self.editors[spec.name] = editor
            if spec.kind == 'bool':
                form.addRow('', editor.widget)
            else:
                form.addRow(spec.label, editor.widget)
            if spec.help and spec.kind not in ('str', 'password'):
                hint = QLabel(spec.help)
                hint.setObjectName('hint')
                form.addRow('', hint)
        return self._scroll(inner)

    def _build_cookie_tab(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        search = QLineEdit()
        search.setPlaceholderText('搜索平台')
        search.setClearButtonEnabled(True)
        hint = QLabel('录制抖音建议填写 Cookie；其他平台一般留空即可，遇到风控或需要登录时再填写。')
        hint.setObjectName('hint')
        inner = QWidget()
        form = QFormLayout(inner)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for spec in COOKIE_SPECS:
            label = QLabel(spec.label)
            edit = QLineEdit()
            edit.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
            edit.textChanged.connect(self._mark_dirty)
            form.addRow(label, edit)
            self.cookie_edits[spec.key] = edit
            self.cookie_rows[spec.key] = (label, edit)

        def apply_filter(text: str) -> None:
            text = text.strip().lower()
            for spec in COOKIE_SPECS:
                visible = not text or text in spec.label.lower() or text in spec.key
                form.setRowVisible(self.cookie_rows[spec.key][1], visible)

        search.textChanged.connect(apply_filter)
        layout.addWidget(hint)
        layout.addWidget(search)
        layout.addWidget(self._scroll(inner), 1)
        return wrapper

    # ------------------------------------------------------------------ state

    def _mark_dirty(self, *_args) -> None:
        if not self._loading:
            self._dirty = True
            self.status_label.setText('有未保存的修改')

    def load(self, settings: Settings) -> None:
        self._loading = True
        try:
            for name, editor in self.editors.items():
                editor.set(settings.get(name))
            for key, edit in self.cookie_edits.items():
                edit.setText(settings.cookie(key))
        finally:
            self._loading = False
        self._dirty = False
        self.status_label.setText('')

    def _on_event(self, event: Event) -> None:
        if event.type == 'settings_changed' and not self._dirty:
            self.load(self.engine.settings)

    def save(self) -> None:
        current = self.engine.settings
        values = {name: editor.get() for name, editor in self.editors.items()
                  if editor.get() != current.get(name)}
        cookies = {key: edit.text().strip() for key, edit in self.cookie_edits.items()
                   if edit.text().strip() != current.cookie(key)}
        web_host = values.get('web_host', current.web_host)
        web_password = values.get('web_password', current.web_password)
        if web_host not in ('127.0.0.1', 'localhost', '::1') and not web_password:
            QMessageBox.warning(self, '需要访问密码', 'Web 监听地址允许外部访问时，必须设置访问密码。')
            return
        try:
            self.engine.update_settings(values, cookies)
        except Exception as e:
            QMessageBox.critical(self, '保存失败', str(e))
            return
        self._dirty = False
        self.load(self.engine.settings)
        self.status_label.setText(f'已保存 {len(values) + len(cookies)} 项修改，已立即生效')

    def test_push(self) -> None:
        if self._dirty:
            QMessageBox.information(self, '发送测试推送', '请先保存设置再测试。')
            return
        if not self.engine.settings.push_channel_list:
            QMessageBox.information(self, '发送测试推送', '还没有选择推送渠道。')
            return
        self.status_label.setText('正在发送测试推送…')

        def done(result, error):
            if error:
                QMessageBox.critical(self, '测试推送失败', str(error))
                self.status_label.setText('')
                return
            lines = [f'{channel}: {text}' for channel, text in result.items()]
            QMessageBox.information(self, '测试推送结果', '\n'.join(lines) or '没有可用的推送渠道')
            self.status_label.setText('')

        run_in_background(self, self.engine.test_notify, done)
