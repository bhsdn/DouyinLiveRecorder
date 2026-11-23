# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from recorder_core.common import QUALITY_CHOICES
from recorder_core.engine import RecorderEngine
from recorder_core.events import Event
from recorder_core.platforms import platform_names
from recorder_core.settings import COOKIE_SPECS, PUSH_CHANNELS, SPECS

STATIC_DIR = Path(__file__).resolve().parent / 'static'
TOKEN_TTL_SECONDS = 7 * 24 * 3600
LOOPBACK_HOSTS = ('127.0.0.1', 'localhost', '::1')
PRIVATE_EVENT_TYPES = ('settings_changed',)


def is_loopback(host: str) -> bool:
    return host in LOOPBACK_HOSTS


class TokenAuth:
    """Password login issuing bearer tokens. Tokens are invalidated when the password changes."""

    def __init__(self, password_provider: Callable[[], str]):
        self._password = password_provider
        self._tokens: dict[str, tuple[float, str]] = {}

    @staticmethod
    def _fingerprint(password: str) -> str:
        return hashlib.sha256(password.encode('utf-8')).hexdigest()

    @property
    def required(self) -> bool:
        return bool(self._password())

    def login(self, password: str) -> str | None:
        current = self._password()
        if not current or not hmac.compare_digest(password.encode('utf-8'), current.encode('utf-8')):
            return None
        token = secrets.token_urlsafe(32)
        self._tokens[token] = (time.time() + TOKEN_TTL_SECONDS, self._fingerprint(current))
        return token

    def check(self, token: str | None) -> bool:
        if not self.required:
            return True
        if not token or token not in self._tokens:
            return False
        expires, fingerprint = self._tokens[token]
        if expires < time.time() or fingerprint != self._fingerprint(self._password()):
            self._tokens.pop(token, None)
            return False
        return True

    def logout(self, token: str | None) -> None:
        if token:
            self._tokens.pop(token, None)


def token_from_request(request: Request, token: str | None = None) -> str | None:
    header = request.headers.get('authorization', '')
    if header.lower().startswith('bearer '):
        return header[7:].strip()
    return token


class EventHub:
    """Fans engine events (raised on worker threads) out to connected websockets."""

    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.queue: asyncio.Queue | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queue = asyncio.Queue(maxsize=5000)

    def publish(self, event: Event) -> None:
        if not self.loop or not self.queue or not self.clients:
            return
        payload = {'type': event.type, 'ts': event.ts} if event.type in PRIVATE_EVENT_TYPES else event.to_dict()

        def put():
            if not self.queue.full():
                self.queue.put_nowait(payload)

        self.loop.call_soon_threadsafe(put)

    async def run(self) -> None:
        while True:
            payload = await self.queue.get()
            for ws in list(self.clients):
                try:
                    await ws.send_json(payload)
                except Exception:
                    self.clients.discard(ws)


class RoomIn(BaseModel):
    url: str
    quality: str | None = None
    name: str = ''
    enabled: bool = True


class RoomPatch(BaseModel):
    url: str | None = None
    quality: str | None = None
    name: str | None = None
    enabled: bool | None = None


class ImportIn(BaseModel):
    text: str
    quality: str | None = None


class SettingsPatch(BaseModel):
    values: dict[str, Any] = {}
    cookies: dict[str, str] = {}


class LoginIn(BaseModel):
    password: str


class PathIn(BaseModel):
    path: str


class NotifyIn(BaseModel):
    channels: list[str] | None = None


def create_app(engine: RecorderEngine, manage_engine: bool = True,
               password_override: str | None = None) -> FastAPI:
    auth = TokenAuth(lambda: password_override if password_override is not None else engine.settings.web_password)
    hub = EventHub()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        hub.attach(asyncio.get_running_loop())
        unsubscribe = engine.bus.subscribe(hub.publish)
        task = asyncio.create_task(hub.run())
        if manage_engine:
            engine.start()
        try:
            yield
        finally:
            task.cancel()
            unsubscribe()
            if manage_engine:
                await asyncio.to_thread(engine.shutdown)

    app = FastAPI(title='DouyinLiveRecorder', lifespan=lifespan, docs_url='/api/docs', redoc_url=None)

    def require_auth(request: Request, token: str | None = Query(default=None)) -> None:
        if not auth.check(token_from_request(request, token)):
            raise HTTPException(status_code=401, detail='未登录或登录已过期')

    @app.exception_handler(ValueError)
    async def value_error(_request, exc):
        return JSONResponse(status_code=400, content={'detail': str(exc)})

    @app.exception_handler(KeyError)
    async def key_error(_request, exc):
        return JSONResponse(status_code=404, content={'detail': str(exc.args[0] if exc.args else exc)})

    @app.exception_handler(PermissionError)
    async def permission_error(_request, exc):
        return JSONResponse(status_code=403, content={'detail': str(exc)})

    @app.exception_handler(FileNotFoundError)
    async def not_found(_request, exc):
        return JSONResponse(status_code=404, content={'detail': f'文件不存在: {exc}'})

    # ------------------------------------------------------------------ auth

    @app.get('/api/auth')
    def auth_status(request: Request, token: str | None = None):
        return {'required': auth.required, 'authenticated': auth.check(token_from_request(request, token))}

    @app.post('/api/login')
    async def login(body: LoginIn):
        token = auth.login(body.password)
        if not token:
            await asyncio.sleep(1)
            raise HTTPException(status_code=401, detail='密码错误')
        return {'token': token}

    @app.post('/api/logout')
    def logout(request: Request):
        auth.logout(token_from_request(request))
        return {'ok': True}

    protected = [Depends(require_auth)]

    # ------------------------------------------------------------------ rooms

    def room_status(room_id: str) -> dict:
        return next(s for s in engine.snapshot() if s['id'] == room_id)

    @app.get('/api/rooms', dependencies=protected)
    def list_rooms():
        return engine.snapshot()

    @app.post('/api/rooms', dependencies=protected)
    def add_room(body: RoomIn):
        room = engine.add_room(body.url, body.quality, body.name, body.enabled)
        return room_status(room.id)

    @app.post('/api/rooms/import', dependencies=protected)
    def import_rooms(body: ImportIn):
        added, skipped = engine.add_rooms_text(body.text, body.quality)
        return {'added': len(added), 'skipped': skipped}

    @app.patch('/api/rooms/{room_id}', dependencies=protected)
    def update_room(room_id: str, body: RoomPatch):
        engine.update_room(room_id, **body.model_dump(exclude_none=True))
        return room_status(room_id)

    @app.delete('/api/rooms/{room_id}', dependencies=protected)
    def delete_room(room_id: str):
        engine.remove_room(room_id)
        return {'ok': True}

    room_actions = {
        'pause': engine.pause_room,
        'resume': engine.resume_room,
        'check': engine.check_now,
        'stop': engine.stop_recording,
    }

    @app.post('/api/rooms/{room_id}/{action}', dependencies=protected)
    def room_action(room_id: str, action: str):
        if action not in room_actions:
            raise HTTPException(status_code=404, detail=f'未知操作: {action}')
        room_actions[action](room_id)
        return room_status(room_id)

    @app.post('/api/bulk/{action}', dependencies=protected)
    def bulk_action(action: str):
        if action == 'pause_all':
            engine.pause_all()
        elif action == 'resume_all':
            engine.resume_all()
        else:
            raise HTTPException(status_code=404, detail=f'未知操作: {action}')
        return {'ok': True}

    # ------------------------------------------------------------------ settings

    @app.get('/api/meta', dependencies=protected)
    def meta():
        return {
            'qualities': QUALITY_CHOICES,
            'platforms': platform_names(),
            'push_channels': PUSH_CHANNELS,
            'specs': [asdict(spec) for spec in SPECS],
            'cookie_specs': [asdict(spec) for spec in COOKIE_SPECS],
        }

    @app.get('/api/settings', dependencies=protected)
    def get_settings():
        return engine.settings.to_dict()

    @app.patch('/api/settings', dependencies=protected)
    def patch_settings(body: SettingsPatch):
        current = engine.settings
        host = body.values.get('web_host', current.web_host)
        password = body.values.get('web_password', current.web_password)
        if password_override is None and not is_loopback(str(host)) and not password:
            raise HTTPException(status_code=400, detail='Web 监听地址允许外部访问时，必须设置访问密码')
        return engine.update_settings(body.values, body.cookies).to_dict()

    @app.post('/api/notify/test', dependencies=protected)
    def notify_test(body: NotifyIn):
        return engine.test_notify(body.channels)

    # ------------------------------------------------------------------ files / logs / system

    @app.get('/api/recordings', dependencies=protected)
    def recordings():
        return [{k: v for k, v in item.items() if k != 'path'} for item in engine.list_recordings()]

    @app.get('/api/recordings/download', dependencies=protected)
    def download(path: str):
        target = engine.resolve_recording(path)
        return FileResponse(target, filename=target.name)

    @app.delete('/api/recordings', dependencies=protected)
    def delete_recording(path: str):
        engine.delete_recording(path)
        return {'ok': True}

    @app.post('/api/recordings/convert', dependencies=protected)
    def convert_recording(body: PathIn):
        engine.convert_recording(body.path)
        return {'ok': True}

    @app.get('/api/logs', dependencies=protected)
    def logs(limit: int = 500):
        return [event.to_dict() for event in list(engine.bus.logs)[-limit:]]

    @app.get('/api/system', dependencies=protected)
    def system():
        return engine.system_info()

    @app.post('/api/system/proxy-check', dependencies=protected)
    def proxy_check():
        return {'global_proxy': engine.check_global_proxy()}

    @app.websocket('/ws/events')
    async def events(ws: WebSocket, token: str | None = None):
        if not auth.check(token):
            await ws.close(code=4401)
            return
        await ws.accept()
        hub.clients.add(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(ws)

    app.mount('/', StaticFiles(directory=STATIC_DIR, html=True), name='static')
    return app
