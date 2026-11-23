# -*- coding: utf-8 -*-
"""Build the CLI, desktop GUI and web server into a single folder with PyInstaller.

    pip install -r requirements.txt -r requirements-gui.txt -r requirements-web.txt pyinstaller
    python packaging/build_all.py [--with-ffmpeg] [--with-node]

Output: dist/DouyinLiveRecorder/
    DouyinLiveRecorder.exe      command line recorder (same as python main.py)
    DouyinLiveRecorderGUI.exe   desktop GUI
    DouyinLiveRecorderWeb.exe   web dashboard
    _internal/                  shared runtime
    config/config.ini.example

--with-ffmpeg / --with-node copy the ffmpeg/ and node/ folders from the project root, so the
package works offline without the automatic download.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist' / 'DouyinLiveRecorder'
EXTRA_FILES = ('StopRecording.vbs', 'index.html', 'LICENSE')
# Created by running the packaged programs; PyInstaller wipes DIST, so these are moved aside and restored.
USER_DATA = ('config/config.ini', 'config/URL_config.ini', 'downloads', 'logs', 'backup_config')


def stash_user_data(stash: Path) -> list[str]:
    moved = []
    for rel in USER_DATA:
        source = DIST / rel
        if source.exists():
            target = stash / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            moved.append(rel)
    return moved


def restore_user_data(stash: Path, moved: list[str]) -> None:
    for rel in moved:
        target = DIST / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        shutil.move(str(stash / rel), str(target))
    shutil.rmtree(stash, ignore_errors=True)


def copy_tool_folder(name: str) -> None:
    source = ROOT / name
    if not source.is_dir():
        print(f'警告：项目根目录下没有 {name}/ 文件夹，已跳过')
        return
    shutil.copytree(source, DIST / name, dirs_exist_ok=True)
    print(f'已复制 {name}/')


def main() -> int:
    parser = argparse.ArgumentParser(description='把命令行、桌面端、Web 端打包到同一个文件夹')
    parser.add_argument('--with-ffmpeg', action='store_true', help='一并复制项目根目录下的 ffmpeg/ 文件夹')
    parser.add_argument('--with-node', action='store_true', help='一并复制项目根目录下的 node/ 文件夹')
    args = parser.parse_args()

    command = [
        sys.executable, '-m', 'PyInstaller', str(ROOT / 'packaging' / 'DouyinLiveRecorder.spec'),
        '--noconfirm', '--clean',
        '--distpath', str(ROOT / 'dist'),
        '--workpath', str(ROOT / 'build' / 'all'),
    ]
    stash = ROOT / 'build' / 'user_data_backup'
    moved = stash_user_data(stash)
    if moved:
        print(f'已暂存打包目录中的用户数据: {", ".join(moved)}')
    try:
        print(' '.join(command))
        result = subprocess.run(command, cwd=ROOT)
    finally:
        restore_user_data(stash, moved)
    if result.returncode != 0:
        return result.returncode

    (DIST / 'config').mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'config' / 'config.ini.example', DIST / 'config' / 'config.ini.example')
    for name in EXTRA_FILES:
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, DIST / name)
    docs = ROOT / 'docs'
    if docs.is_dir():
        shutil.copytree(docs, DIST / 'docs', dirs_exist_ok=True)
    if args.with_ffmpeg:
        copy_tool_folder('ffmpeg')
    if args.with_node:
        copy_tool_folder('node')

    print(f'\n构建完成: {DIST}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
