# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: builds the CLI, desktop GUI and web server into one folder sharing _internal/.
# Run via:  python packaging/build_all.py
import os

ROOT = os.path.dirname(SPECPATH)

common_datas = [
    (os.path.join(ROOT, 'src', 'javascript'), os.path.join('src', 'javascript')),
    (os.path.join(ROOT, 'i18n'), 'i18n'),
]
web_datas = [(os.path.join(ROOT, 'web', 'static'), os.path.join('web', 'static'))]

QT_MODULES = ['PySide6', 'shiboken6']
WEB_MODULES = ['web', 'fastapi', 'uvicorn', 'starlette', 'websockets']
NEVER = ['tkinter', 'pytest', 'ruff', 'PyInstaller']


def analysis(script, datas, excludes):
    return Analysis(
        [os.path.join(ROOT, script)],
        pathex=[ROOT],
        datas=datas,
        excludes=excludes + NEVER,
        noarchive=False,
    )


cli_a = analysis('main.py', common_datas, QT_MODULES + WEB_MODULES)
gui_a = analysis('gui.py', common_datas, WEB_MODULES)
web_a = analysis('web_server.py', common_datas + web_datas, QT_MODULES)


def exe(a, name, console):
    pyz = PYZ(a.pure)
    return EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        console=console,
        upx=False,
    )


cli_exe = exe(cli_a, 'DouyinLiveRecorder', console=True)
gui_exe = exe(gui_a, 'DouyinLiveRecorderGUI', console=False)
web_exe = exe(web_a, 'DouyinLiveRecorderWeb', console=True)

coll = COLLECT(
    cli_exe, gui_exe, web_exe,
    cli_a.binaries + gui_a.binaries + web_a.binaries,
    cli_a.datas + gui_a.datas + web_a.datas,
    upx=False,
    name='DouyinLiveRecorder',
)
