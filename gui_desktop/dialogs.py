# -*- coding: utf-8 -*-
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from recorder_core.common import QUALITY_CHOICES
from recorder_core.platforms import find_platform, is_supported_url
from recorder_core.rooms import normalize_url


class RoomDialog(QDialog):
    def __init__(self, parent=None, url: str = '', quality: str = '原画', name: str = '', enabled: bool = True,
                 title: str = '添加直播间'):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)

        self.url_edit = QLineEdit(url)
        self.url_edit.setPlaceholderText('例如 https://live.douyin.com/745964462470（尽量使用 PC 网页端地址）')
        self.platform_label = QLabel()
        self.platform_label.setObjectName('hint')
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(QUALITY_CHOICES)
        self.quality_combo.setCurrentText(quality)
        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText('留空则首次检测时自动获取主播名')
        self.enabled_check = QCheckBox('启用监测')
        self.enabled_check.setChecked(enabled)

        form = QFormLayout()
        form.addRow('直播间地址', self.url_edit)
        form.addRow('', self.platform_label)
        form.addRow('录制画质', self.quality_combo)
        form.addRow('主播名称', self.name_edit)
        form.addRow('', self.enabled_check)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self.url_edit.textChanged.connect(self._update_platform)
        self._update_platform()

    def _update_platform(self) -> None:
        text = self.url_edit.text().strip()
        if not text:
            self.platform_label.setText('')
            return
        url = normalize_url(text)
        platform = find_platform(url) if is_supported_url(url) else None
        self.platform_label.setText(f'识别为：{platform.name}' if platform else '无法识别该地址，暂不支持')

    def _accept(self) -> None:
        url = normalize_url(self.url_edit.text())
        if not is_supported_url(url):
            QMessageBox.warning(self, '地址无效', '不支持该直播间地址，请检查后重试。')
            return
        self.accept()

    def values(self) -> dict:
        return {
            'url': normalize_url(self.url_edit.text()),
            'quality': self.quality_combo.currentText(),
            'name': self.name_edit.text().strip(),
            'enabled': self.enabled_check.isChecked(),
        }


class BatchImportDialog(QDialog):
    def __init__(self, parent=None, default_quality: str = '原画'):
        super().__init__(parent)
        self.setWindowTitle('批量导入直播间')
        self.resize(620, 420)

        hint = QLabel('每行一个，格式与 URL_config.ini 相同：[画质,]直播间地址[,主播: 名称]，行首加 # 表示暂停。')
        hint.setWordWrap(True)
        hint.setObjectName('hint')
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(
            'https://live.douyin.com/745964462470\n超清,https://www.huya.com/116,主播: 张三')
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(QUALITY_CHOICES)
        self.quality_combo.setCurrentText(default_quality)

        form = QFormLayout()
        form.addRow('未指定画质时使用', self.quality_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(self.text_edit)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str]:
        return self.text_edit.toPlainText(), self.quality_combo.currentText()
