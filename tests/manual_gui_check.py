# -*- coding: utf-8 -*-
"""Manual smoke test of the desktop GUI; saves screenshots of every page.

    python tests/manual_gui_check.py <out_dir> [live_url]

Uses a temporary home directory. Set QT_QPA_PLATFORM=offscreen to run headless.
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DLR_SKIP_AUTO_INSTALL', '1')


def main(out_dir: str, live_url: str | None) -> int:
    home = Path(tempfile.mkdtemp(prefix='dlr_gui_'))
    (home / 'config').mkdir()
    shutil.copy(ROOT / 'config' / 'config.ini.example', home / 'config' / 'config.ini.example')
    lines = ['https://live.bilibili.com/21593109', '#超清,https://live.douyin.com/745964462470,主播: 已暂停示例']
    if live_url:
        lines.insert(0, live_url)
    (home / 'config' / 'URL_config.ini').write_text('\n'.join(lines) + '\n', encoding='utf-8-sig')

    from PySide6.QtWidgets import QApplication

    from gui_desktop.main_window import MainWindow
    from recorder_core.engine import RecorderEngine
    from recorder_core.paths import AppPaths

    app = QApplication(sys.argv)
    engine = RecorderEngine(AppPaths(home), detect_proxy=False)
    engine.update_settings({'split_enabled': False})
    window = MainWindow(engine)
    window.resize(1360, 760)
    window.show()
    engine.start()

    def pump(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.05)

    deadline = time.time() + 40
    while time.time() < deadline:
        pump(0.5)
        states = [s['state'] for s in engine.snapshot()]
        if live_url and 'recording' in states:
            break
        if not live_url and 'checking' not in states and 'idle' not in states:
            break
    pump(6)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for index, (label, _) in enumerate(window.pages):
        window.nav.setCurrentRow(index)
        pump(1.5)
        window.grab().save(str(out / f'{index}_{label}.png'))
        print('saved', out / f'{index}_{label}.png')
    print('states:', [(s['name'], s['state_label']) for s in engine.snapshot()])

    window._quitting = True
    engine.shutdown(timeout=20)
    window.bridge.close()
    files = [p for p in (home / 'downloads').rglob('*') if p.is_file()]
    print('files:', [f'{p.name} {p.stat().st_size}' for p in files])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
