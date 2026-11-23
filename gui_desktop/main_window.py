# -*- coding: utf-8 -*-
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QStackedWidget,
    QSystemTrayIcon,
    QWidget,
)

from recorder_core import VERSION
from recorder_core.engine import RecorderEngine, tool_version
from recorder_core.events import Event

from .common import STYLE_SHEET, EngineBridge, app_icon, run_in_background
from .files_page import FilesPage
from .logs_page import LogsPage
from .rooms_page import RoomsPage
from .settings_page import SettingsPage
from .system_page import SystemPage, install_ffmpeg, install_node

CLOSE_ASK, CLOSE_TRAY, CLOSE_QUIT = 'ask', 'tray', 'quit'


class MainWindow(QMainWindow):
    def __init__(self, engine: RecorderEngine):
        super().__init__()
        self.engine = engine
        self.prefs = QSettings('DouyinLiveRecorder', 'DesktopGUI')
        self._quitting = False
        self.tray: QSystemTrayIcon | None = None
        self.bridge = EngineBridge(engine.bus)

        self.setWindowTitle(f'DouyinLiveRecorder {VERSION} - 直播录制')
        self.setWindowIcon(app_icon())
        self.setStyleSheet(STYLE_SHEET)
        self.resize(1280, 780)
        geometry = self.prefs.value('geometry')
        if geometry:
            self.restoreGeometry(geometry)

        self.rooms_page = RoomsPage(engine, self.bridge)
        self.pages = [
            ('直播间', self.rooms_page),
            ('录制文件', FilesPage(engine, self.bridge)),
            ('运行日志', LogsPage(engine, self.bridge)),
            ('设置', SettingsPage(engine, self.bridge)),
            ('环境与关于', SystemPage(engine)),
        ]
        self.nav = QListWidget()
        self.nav.setObjectName('nav')
        self.nav.setFixedWidth(150)
        self.stack = QStackedWidget()
        for label, page in self.pages:
            self.nav.addItem(label)
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.nav)
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(16, 12, 16, 8)
        content_layout.addWidget(self.stack)
        layout.addWidget(content, 1)
        self.setCentralWidget(central)

        self.status_labels = {key: QLabel() for key in ('monitor', 'recording', 'disk', 'proxy')}
        for label in self.status_labels.values():
            self.statusBar().addPermanentWidget(label)
        self._update_status()
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._update_status)
        self.status_timer.start(2000)

        self._setup_tray()
        self.bridge.event.connect(self._on_event)

    # ------------------------------------------------------------------ status / tray

    def _update_status(self) -> None:
        statuses = self.engine.snapshot()
        monitoring = sum(1 for s in statuses if s['enabled'])
        recording = sum(1 for s in statuses if s['state'] == 'recording')
        self.status_labels['monitor'].setText(f'  监测 {monitoring}/{len(statuses)}  ')
        self.status_labels['recording'].setText(f'  录制中 {recording}  ')
        try:
            free_gb, _ = self.engine.disk_usage()
            warn = ' (空间不足，已暂停录制)' if self.engine.disk_full else ''
            self.status_labels['disk'].setText(f'  磁盘剩余 {free_gb:.1f} GB{warn}  ')
        except OSError:
            self.status_labels['disk'].setText('')
        if not self.engine.proxy_checked:
            proxy = '代理检测中…'
        else:
            proxy = '海外网络可用' if self.engine.global_proxy else '未检测到代理'
        self.status_labels['proxy'].setText(f'  {proxy}  ')
        if self.tray:
            self.tray.setToolTip(f'DouyinLiveRecorder\n监测 {monitoring} 个，录制中 {recording} 个')

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        for text, slot in (('显示主窗口', self.show_normal), ('全部暂停', self.engine.pause_all),
                           ('全部恢复', self.engine.resume_all)):
            action = QAction(text, menu)
            action.triggered.connect(slot)
            menu.addAction(action)
        menu.addSeparator()
        quit_action = QAction('退出', menu)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_normal() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()
        self._tray_menu = menu

    def show_normal(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _notify(self, title: str, message: str) -> None:
        if self.tray and self.prefs.value('tray_notify', True, type=bool):
            self.tray.showMessage(title, message, app_icon(), 5000)

    def _on_event(self, event: Event) -> None:
        if event.type == 'recording_started':
            room = event.data['room']
            self._notify('开始录制', f"{room['name']}（{room['platform']}）")
        elif event.type == 'recording_finished' and not event.data['stopped']:
            room = event.data['room']
            if event.data['return_code'] == 0:
                self._notify('录制完成', f"{room['name']} 的直播已结束，文件已保存")
            else:
                self._notify('录制出错', f"{room['name']}：{event.data.get('error') or '录制异常结束'}")
        elif event.type == 'disk_low':
            self._notify('磁盘空间不足', f"剩余 {event.data['free_gb']:.1f} GB，已停止录制")

    # ------------------------------------------------------------------ environment

    def check_environment(self) -> None:
        def done(result, error):
            if error:
                return
            ffmpeg, node = result
            if not ffmpeg:
                answer = QMessageBox.question(
                    self, '缺少 ffmpeg', '未检测到 ffmpeg，无法录制直播。\n是否现在自动下载安装？')
                if answer == QMessageBox.StandardButton.Yes:
                    self._install('ffmpeg', install_ffmpeg)
            elif not node:
                answer = QMessageBox.question(
                    self, '缺少 Node.js', '未检测到 Node.js，抖音等平台的签名计算需要它。\n是否现在自动下载安装？')
                if answer == QMessageBox.StandardButton.Yes:
                    self._install('Node.js', install_node)

        run_in_background(self, lambda: (tool_version(['ffmpeg', '-version']), tool_version(['node', '-v'])),
                          done)

    def _install(self, name: str, func) -> None:
        self.statusBar().showMessage(f'正在安装 {name}，请稍候…')

        def done(ok, error):
            self.statusBar().clearMessage()
            if error or not ok:
                QMessageBox.warning(self, f'安装 {name}', f'自动安装失败，请手动安装后重启程序。\n{error or ""}')
            else:
                QMessageBox.information(self, f'安装 {name}', f'{name} 已安装。')
            self.check_environment()

        run_in_background(self, func, done)

    # ------------------------------------------------------------------ closing

    def closeEvent(self, event: QCloseEvent) -> None:
        self.prefs.setValue('geometry', self.saveGeometry())
        if self._quitting or not self.tray:
            if not self._quitting:
                event.ignore()
                self.quit_app()
            return

        action = self.prefs.value('close_action', CLOSE_ASK)
        if action == CLOSE_ASK:
            box = QMessageBox(self)
            box.setWindowTitle('关闭窗口')
            box.setText('关闭窗口后要继续在后台录制吗？')
            tray_button = box.addButton('最小化到托盘', QMessageBox.ButtonRole.AcceptRole)
            quit_button = box.addButton('退出程序', QMessageBox.ButtonRole.DestructiveRole)
            box.addButton('取消', QMessageBox.ButtonRole.RejectRole)
            remember = QCheckBox('记住我的选择')
            box.setCheckBox(remember)
            box.exec()
            clicked = box.clickedButton()
            if clicked is tray_button:
                action = CLOSE_TRAY
            elif clicked is quit_button:
                action = CLOSE_QUIT
            else:
                event.ignore()
                return
            if remember.isChecked():
                self.prefs.setValue('close_action', action)

        event.ignore()
        if action == CLOSE_TRAY:
            self.hide()
            self._notify('仍在后台运行', '双击托盘图标可重新打开窗口')
        else:
            self.quit_app()

    def quit_app(self) -> None:
        if self._quitting:
            return
        recording = sum(1 for s in self.engine.snapshot() if s['state'] == 'recording')
        if recording:
            self.show_normal()
            answer = QMessageBox.question(
                self, '退出程序', f'有 {recording} 个直播间正在录制，退出将停止录制并保存文件。确定退出吗？')
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._quitting = True
        self.status_timer.stop()
        progress = QProgressDialog('正在停止录制并保存文件…', None, 0, 0, self)
        progress.setWindowTitle('正在退出')
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.show()

        def done(*_):
            progress.close()
            self.bridge.close()
            if self.tray:
                self.tray.hide()
            QApplication.quit()

        run_in_background(self, self.engine.shutdown, done)
