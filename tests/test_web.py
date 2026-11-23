# -*- coding: utf-8 -*-
import pytest
from fastapi.testclient import TestClient

from recorder_core.engine import RecorderEngine
from recorder_core.paths import AppPaths
from recorder_core.platforms import Platform
from web.server import create_app

URL = 'https://live.douyin.com/1234567890'


async def offline(ctx):
    return {'anchor_name': '主播A', 'is_live': False}


@pytest.fixture
def engine(home):
    platform = Platform(name='抖音直播', match=('douyin.com/',), fetch=offline)
    engine = RecorderEngine(AppPaths(home), platform_finder=lambda url: platform, watch_files=False,
                            detect_proxy=False)
    engine.update_settings({'loop_interval': 600})
    yield engine
    engine.shutdown(timeout=5)


def test_auth_required_when_password_set(engine):
    engine.update_settings({'web_password': 'secret'})
    with TestClient(create_app(engine)) as client:
        assert client.get('/api/auth').json() == {'required': True, 'authenticated': False}
        assert client.get('/api/rooms').status_code == 401
        assert client.post('/api/login', json={'password': 'wrong'}).status_code == 401
        token = client.post('/api/login', json={'password': 'secret'}).json()['token']
        headers = {'Authorization': f'Bearer {token}'}
        assert client.get('/api/rooms', headers=headers).status_code == 200

        engine.update_settings({'web_password': 'changed'})
        assert client.get('/api/rooms', headers=headers).status_code == 401


def test_room_crud_and_actions(engine):
    with TestClient(create_app(engine)) as client:
        assert client.get('/api/auth').json()['required'] is False
        room = client.post('/api/rooms', json={'url': URL, 'quality': '超清'}).json()
        assert room['quality'] == '超清' and room['platform'] == '抖音直播'
        assert client.post('/api/rooms', json={'url': URL}).status_code == 400
        assert client.post('/api/rooms', json={'url': 'https://example.com/a/b/c'}).status_code == 400

        paused = client.post(f"/api/rooms/{room['id']}/pause").json()
        assert paused['enabled'] is False
        assert client.patch(f"/api/rooms/{room['id']}", json={'name': '新名字'}).json()['name'] == '新名字'
        assert client.post(f"/api/rooms/{room['id']}/explode").status_code == 404

        imported = client.post('/api/rooms/import', json={'text': 'https://live.douyin.com/999999999\nbad line'})
        assert imported.json()['added'] == 1
        assert len(client.get('/api/rooms').json()) == 2

        assert client.delete(f"/api/rooms/{room['id']}").status_code == 200
        assert client.delete(f"/api/rooms/{room['id']}").status_code == 404


def test_settings_patch_and_validation(engine):
    with TestClient(create_app(engine)) as client:
        meta = client.get('/api/meta').json()
        assert any(spec['name'] == 'save_format' for spec in meta['specs'])
        assert len(meta['cookie_specs']) == 50

        res = client.patch('/api/settings', json={'values': {'loop_interval': 90}, 'cookies': {'douyin': 'a=b'}})
        assert res.status_code == 200
        assert res.json()['values']['loop_interval'] == 90
        assert engine.settings.cookie('douyin') == 'a=b'

        bad = client.patch('/api/settings', json={'values': {'web_host': '0.0.0.0'}})
        assert bad.status_code == 400
        assert client.patch('/api/settings', json={'values': {'nope': 1}}).status_code == 404


def test_recordings_listing_download_and_traversal(engine):
    engine.save_root.mkdir(parents=True, exist_ok=True)
    (engine.save_root / 'p').mkdir()
    (engine.save_root / 'p' / 'a.ts').write_bytes(b'abc')
    with TestClient(create_app(engine)) as client:
        items = client.get('/api/recordings').json()
        assert items[0]['relative'] == 'p/a.ts' and 'path' not in items[0]
        assert client.get('/api/recordings/download', params={'path': 'p/a.ts'}).content == b'abc'
        assert client.get('/api/recordings/download', params={'path': '../config/config.ini'}).status_code == 403
        assert client.delete('/api/recordings', params={'path': 'p/a.ts'}).status_code == 200
        assert not (engine.save_root / 'p' / 'a.ts').exists()


def test_websocket_streams_room_events(engine):
    with TestClient(create_app(engine)) as client:
        with client.websocket_connect('/ws/events') as ws:
            client.post('/api/rooms', json={'url': URL})
            seen = set()
            for _ in range(20):
                event = ws.receive_json()
                seen.add(event['type'])
                if event['type'] == 'room_state' and event['room']['state'] == 'waiting':
                    break
            assert 'room_added' in seen and 'room_state' in seen


def test_static_index_served(engine):
    with TestClient(create_app(engine)) as client:
        res = client.get('/')
        assert res.status_code == 200 and 'vendor/vue.global.prod.js' in res.text
