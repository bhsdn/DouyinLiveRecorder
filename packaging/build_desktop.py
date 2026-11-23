# -*- coding: utf-8 -*-
"""Build the desktop GUI into a folder distribution with PyInstaller.

    pip install -r requirements.txt -r requirements-gui.txt pyinstaller
    python packaging/build_desktop.py

Output: dist/DouyinLiveRecorderGUI/DouyinLiveRecorderGUI(.exe)
The config/ folder is created next to the executable on first run. Put ffmpeg/ and node/
folders next to the executable to ship them, or let the program install them.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = 'DouyinLiveRecorderGUI'


def main() -> int:
    sep = os.pathsep
    command = [
        sys.executable, '-m', 'PyInstaller', str(ROOT / 'gui.py'),
        '--name', NAME,
        '--noconfirm', '--clean', '--windowed', '--onedir',
        '--add-data', f'{ROOT / "src" / "javascript"}{sep}src/javascript',
        '--add-data', f'{ROOT / "i18n"}{sep}i18n',
        '--exclude-module', 'web',
        '--exclude-module', 'fastapi',
        '--exclude-module', 'uvicorn',
        '--distpath', str(ROOT / 'dist'),
        '--workpath', str(ROOT / 'build'),
        '--specpath', str(ROOT / 'build'),
    ]
    print(' '.join(command))
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    dist = ROOT / 'dist' / NAME
    (dist / 'config').mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'config' / 'config.ini.example', dist / 'config' / 'config.ini.example')
    print(f'\n构建完成: {dist}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
