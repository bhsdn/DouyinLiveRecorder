# -*- coding: utf-8 -*-
"""Manual end-to-end check against a real, currently live room.

    python tests/manual_record_check.py <live_url> [seconds]

Records in a temporary home directory, then stops by commenting the line in URL_config.ini
(the original CLI workflow) and verifies the output. Needs ffmpeg and network access.
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


def main(url: str, seconds: int) -> int:
    home = Path(tempfile.mkdtemp(prefix='dlr_e2e_'))
    os.environ['DLR_HOME'] = str(home)
    (home / 'config').mkdir()
    shutil.copy(ROOT / 'config' / 'config.ini.example', home / 'config' / 'config.ini.example')
    (home / 'config' / 'URL_config.ini').write_text(url + '\n', encoding='utf-8-sig')

    from recorder_core.engine import RecorderEngine
    from recorder_core.paths import AppPaths

    engine = RecorderEngine(AppPaths(home), detect_proxy=False, background_interval=1)
    engine.update_settings({'convert_to_mp4': True, 'delete_origin': False, 'loop_interval': 30})
    engine.bus.subscribe(lambda e: e.type == 'log' and print(f"[{e.data['level']}] {e.data['message']}"))
    engine.start()

    deadline = time.time() + 60
    while time.time() < deadline and not any(s['state'] == 'recording' for s in engine.snapshot()):
        time.sleep(1)
    status = engine.snapshot()[0]
    if status['state'] != 'recording':
        print('NOT RECORDING:', status)
        engine.shutdown()
        return 1
    print('recording to', status['record_file'])
    time.sleep(seconds)

    url_file = home / 'config' / 'URL_config.ini'
    url_file.write_text('#' + url_file.read_text(encoding='utf-8-sig'), encoding='utf-8-sig')
    deadline = time.time() + 40
    while time.time() < deadline and engine.snapshot()[0]['state'] != 'paused':
        time.sleep(1)
    print('state after comment:', engine.snapshot()[0]['state'])
    time.sleep(8)
    engine.shutdown()

    files = sorted(p for p in (home / 'downloads').rglob('*') if p.is_file())
    for p in files:
        print(f'  {p.relative_to(home)}  {p.stat().st_size} bytes')
    print('URL_config.ini:', url_file.read_text(encoding='utf-8-sig').strip())
    print('home:', home)
    return 0 if any(p.suffix == '.mp4' and p.stat().st_size > 0 for p in files) else 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 15))
