# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from recorder_core.engine import RecorderEngine
from recorder_core.platforms import platform_names

from .common import open_path, run_in_background

PROJECT_URL = 'https://github.com/ihmily/DouyinLiveRecorder'


def install_ffmpeg() -> bool:
    from ffmpeg_install import check_ffmpeg
    return bool(check_ffmpeg())


def install_node() -> bool:
    from src.initializer import check_node, check_nodejs_installed
    check_node()
    return check_nodejs_installed()


class SystemPage(QWidget):
    def __init__(self, engine: RecorderEngine):
        super().__init__()
        self.engine = engine

        title = QLabel('环境与关于')
        title.setObjectName('pageTitle')

        self.labels: dict[str, QLabel] = {}
        env_box = QGroupBox('运行环境')
        form = QFormLayout(env_box)
        for key, text in (('version', '版本'), ('ffmpeg', 'ffmpeg'), ('node', 'Node.js'),
                          ('proxy', '全局代理'), ('save_root', '录制目录'), ('disk', '磁盘空间'),
                          ('concurrency', '并发检测数'), ('started', '启动时间')):
            label = QLabel('…')
            label.setWordWrap(True)
            form.addRow(text, label)
            self.labels[key] = label

        self.ffmpeg_button = QPushButton('安装 ffmpeg')
        self.ffmpeg_button.clicked.connect(lambda: self._install('ffmpeg', install_ffmpeg))
        self.node_button = QPushButton('安装 Node.js')
        self.node_button.clicked.connect(lambda: self._install('Node.js', install_node))
        refresh_button = QPushButton('刷新')
        refresh_button.clicked.connect(self.refresh)
        proxy_button = QPushButton('重新检测代理')
        proxy_button.clicked.connect(self._recheck_proxy)
        buttons = QHBoxLayout()
        for button in (refresh_button, proxy_button, self.ffmpeg_button, self.node_button):
            buttons.addWidget(button)
        buttons.addStretch(1)

        platform_box = QGroupBox(f'支持的平台（{len(platform_names())}）')
        platform_label = QLabel('、'.join(platform_names()))
        platform_label.setWordWrap(True)
        QVBoxLayout(platform_box).addWidget(platform_label)

        about_box = QGroupBox('关于')
        about_layout = QVBoxLayout(about_box)
        about_layout.addWidget(QLabel('基于 DouyinLiveRecorder 二次开发，录制核心与命令行版、Web 管理端共用。'))
        link_row = QHBoxLayout()
        home_button = QPushButton('项目主页')
        home_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(PROJECT_URL)))
        config_button = QPushButton('打开程序目录')
        config_button.clicked.connect(lambda: open_path(self.engine.paths.base))
        link_row.addWidget(home_button)
        link_row.addWidget(config_button)
        link_row.addStretch(1)
        about_layout.addLayout(link_row)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(env_box)
        layout.addLayout(buttons)
        layout.addWidget(platform_box)
        layout.addWidget(about_box)
        layout.addStretch(1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        def done(info, error):
            if error:
                return
            self.labels['version'].setText(info['version'])
            self.labels['ffmpeg'].setText(info['ffmpeg'] or '未检测到，无法录制')
            self.labels['node'].setText(info['node'] or '未检测到，部分平台(抖音等)签名需要 Node.js')
            if not info['proxy_checked']:
                proxy = '检测中…'
            else:
                proxy = '可访问海外网站' if info['global_proxy'] else '未检测到（不录制海外平台可忽略）'
            self.labels['proxy'].setText(proxy)
            self.labels['save_root'].setText(info['save_root'])
            disk = f"剩余 {info['disk_free_gb']} GB / 共 {info['disk_total_gb']} GB"
            if info['disk_full']:
                disk += '（低于阈值，已暂停录制）'
            self.labels['disk'].setText(disk)
            self.labels['concurrency'].setText(str(info['max_request']))
            started = info['started_at']
            self.labels['started'].setText(
                datetime.datetime.fromtimestamp(started).strftime('%Y-%m-%d %H:%M:%S') if started else '')
            self.ffmpeg_button.setVisible(not info['ffmpeg'])
            self.node_button.setVisible(not info['node'])

        run_in_background(self, self.engine.system_info, done)

    def _recheck_proxy(self) -> None:
        self.labels['proxy'].setText('检测中…')
        run_in_background(self, self.engine.check_global_proxy, lambda *_: self.refresh())

    def _install(self, name: str, func) -> None:
        if QMessageBox.question(self, f'安装 {name}', f'将自动下载并安装 {name}，可能需要几分钟，是否继续？') != \
                QMessageBox.StandardButton.Yes:
            return
        self.ffmpeg_button.setEnabled(False)
        self.node_button.setEnabled(False)

        def done(ok, error):
            self.ffmpeg_button.setEnabled(True)
            self.node_button.setEnabled(True)
            if error or not ok:
                QMessageBox.warning(self, f'安装 {name}', f'安装失败，请手动安装后重启程序。\n{error or ""}')
            else:
                QMessageBox.information(self, f'安装 {name}', f'{name} 安装完成。')
            self.refresh()

        run_in_background(self, func, done)
