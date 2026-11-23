# -*- coding: utf-8 -*-
import threading
import time
from pathlib import Path

import pytest

from recorder_core.engine import AdjustableSemaphore, RecorderEngine
from recorder_core.paths import AppPaths
from recorder_core.platforms import Platform

LIVE_URL = 'https://live.douyin.com/1234567890'


class FakeStream:
    def __init__(self):
        self.live = False
        self.ended = threading.Event()

    async def fetch(self, ctx):
        return {'anchor_name': '测试主播', 'is_live': self.live, 'title': '标题',
                'record_url': 'https://cdn/live.m3u8', 'flv_url': 'https://cdn/live.flv?codec=h264'}


class FakeRunner:
    def __init__(self, stream: FakeStream):
        self.stream = stream
        self.commands = []

    def run(self, command, stop_event, on_line=None):
        self.commands.append(command)
        output = Path(command[-1].replace('%03d', '000'))
        output.write_bytes(b'data')
        while not stop_event.is_set() and not self.stream.ended.is_set():
            time.sleep(0.05)
        return 0

    def download(self, url, save_path, headers, stop_event, proxy=None):
        raise AssertionError('unexpected direct download')


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, live_url, content, channels=None):
        self.sent.append(content)
        return {'微信': 'ok'}

    def begin_text(self, name):
        return f'begin {name}'

    def over_text(self, name):
        return f'over {name}'


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def setup(home):
    stream = FakeStream()
    platform = Platform(name='抖音直播', match=('douyin.com/',), fetch=stream.fetch, flv_preferred=True)
    runner = FakeRunner(stream)
    notifier = FakeNotifier()
    engine = RecorderEngine(AppPaths(home), runner=runner, platform_finder=lambda url: platform,
                            notifier=notifier, watch_files=True, detect_proxy=False, background_interval=0.2)
    engine.update_settings({'split_enabled': False, 'convert_to_mp4': False, 'loop_interval': 600,
                            'push_channels': '微信', 'begin_push': True, 'over_push': True})
    events = []
    engine.bus.subscribe(events.append)
    yield engine, stream, runner, notifier, events, home
    engine.shutdown(timeout=5)


def state(engine):
    return engine.snapshot()[0]['state']


def test_full_cycle_waiting_recording_finished(setup):
    engine, stream, runner, notifier, events, home = setup
    room = engine.add_room(LIVE_URL)
    engine.start()
    assert wait_for(lambda: state(engine) == 'waiting')
    assert engine.get_room(room.id).name == '测试主播'
    assert '主播: 测试主播' in (home / 'config' / 'URL_config.ini').read_text(encoding='utf-8-sig')

    stream.live = True
    engine.check_now(room.id)
    assert wait_for(lambda: state(engine) == 'recording')
    command = runner.commands[0]
    assert command[command.index('-i') + 1] == 'https://cdn/live.flv?codec=h264'
    assert command[-1].endswith('.ts') and '/抖音直播/测试主播/' in command[-1]
    assert notifier.sent == ['begin 序号1 测试主播']

    stream.ended.set()
    assert wait_for(lambda: any(e.type == 'recording_finished' for e in events))
    finished = next(e for e in events if e.type == 'recording_finished')
    assert finished.data['return_code'] == 0 and finished.data['stopped'] is False
    assert len(finished.data['files']) == 1


def test_pause_stops_recording_and_comments_line(setup):
    engine, stream, runner, notifier, events, home = setup
    stream.live = True
    room = engine.add_room(LIVE_URL)
    engine.start()
    assert wait_for(lambda: state(engine) == 'recording')
    engine.pause_room(room.id)
    assert wait_for(lambda: state(engine) == 'paused')
    finished = next(e for e in events if e.type == 'recording_finished')
    assert finished.data['stopped'] is True
    assert (home / 'config' / 'URL_config.ini').read_text(encoding='utf-8-sig').startswith('#')

    engine.resume_room(room.id)
    assert wait_for(lambda: state(engine) == 'recording')


def test_stop_recording_skips_current_broadcast(setup):
    engine, stream, runner, notifier, events, home = setup
    stream.live = True
    room = engine.add_room(LIVE_URL)
    engine.start()
    assert wait_for(lambda: state(engine) == 'recording')
    engine.stop_recording(room.id)
    assert wait_for(lambda: state(engine) == 'live')
    engine.check_now(room.id)
    time.sleep(0.3)
    assert state(engine) == 'live'
    assert len(runner.commands) == 1, runner.commands

    stream.live = False
    engine.check_now(room.id)
    assert wait_for(lambda: state(engine) == 'waiting')
    assert wait_for(lambda: notifier.sent[-1] == 'over 序号1 测试主播')


def test_external_file_edit_is_picked_up(setup):
    engine, stream, runner, notifier, events, home = setup
    engine.start()
    url_file = home / 'config' / 'URL_config.ini'
    url_file.write_text(f'超清,{LIVE_URL}\n', encoding='utf-8-sig')
    assert wait_for(lambda: len(engine.rooms()) == 1)
    assert engine.rooms()[0].quality == '超清'

    url_file.write_text(f'#超清,{LIVE_URL}\n', encoding='utf-8-sig')
    assert wait_for(lambda: engine.rooms()[0].enabled is False)


def test_add_room_validation(setup):
    engine = setup[0]
    engine.add_room(LIVE_URL)
    with pytest.raises(ValueError):
        engine.add_room(LIVE_URL)
    with pytest.raises(ValueError):
        engine.add_room('https://example.com/123456789')
    added, skipped = engine.add_rooms_text(f'{LIVE_URL}\n高清,https://www.huya.com/116\nhttps://example.com/xx/yy')
    assert [r.url for r in added] == ['https://www.huya.com/116']
    assert len(skipped) == 2


def test_recordings_are_confined_to_save_root(setup):
    engine = setup[0]
    engine.save_root.mkdir(parents=True, exist_ok=True)
    (engine.save_root / 'a.ts').write_bytes(b'x')
    assert [item['relative'] for item in engine.list_recordings()] == ['a.ts']
    with pytest.raises(PermissionError):
        engine.resolve_recording('../config/config.ini')


def test_adjustable_semaphore_limits_concurrency():
    sem = AdjustableSemaphore(1)
    inside, peak = [0], [0]
    lock = threading.Lock()

    def work():
        with sem.slot():
            with lock:
                inside[0] += 1
                peak[0] = max(peak[0], inside[0])
            time.sleep(0.05)
            with lock:
                inside[0] -= 1

    threads = [threading.Thread(target=work) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1
