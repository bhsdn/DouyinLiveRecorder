# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError

from src.logger import logger

from . import VERSION
from .events import EventBus
from .notify import Notifier
from .paths import AppPaths
from .platforms import Platform, find_platform, is_supported_url
from .postprocess import convert_files_async
from .rooms import Room, RoomStore, normalize_quality, normalize_url, parse_line
from .runner import ProcessRunner
from .settings import BackupManager, ConfigStore, Settings
from .worker import RoomState, RoomWorker

ERROR_WINDOW_SIZE = 10
ERROR_THRESHOLD = 5
LOG_LEVELS = ('debug', 'info', 'warning', 'error')


class AdjustableSemaphore:
    def __init__(self, limit: int):
        self._limit = max(1, int(limit))
        self._in_use = 0
        self._cond = threading.Condition()

    @property
    def limit(self) -> int:
        return self._limit

    def set_limit(self, limit: int) -> None:
        with self._cond:
            self._limit = max(1, int(limit))
            self._cond.notify_all()

    @contextmanager
    def slot(self, cancel: threading.Event | None = None):
        with self._cond:
            while self._in_use >= self._limit:
                if cancel is not None and cancel.is_set():
                    break
                self._cond.wait(timeout=1)
            self._in_use += 1
        try:
            yield
        finally:
            with self._cond:
                self._in_use -= 1
                self._cond.notify()


def ensure_tool_paths() -> None:
    """Put the bundled ffmpeg/node folders (next to the program) on PATH, like the original main.py."""
    from ffmpeg_install import ffmpeg_path
    current = os.environ.get('PATH', '')
    if ffmpeg_path not in current.split(os.pathsep):
        os.environ['PATH'] = ffmpeg_path + os.pathsep + current


def tool_version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, errors='replace', timeout=15,
            stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        output = (result.stdout or result.stderr).strip()
        if result.returncode == 0 and output:
            return output.splitlines()[0].strip()
        logger.warning(f"{command[0]} 检测失败: 返回码 {result.returncode}, 输出: {output[:300]!r}")
    except FileNotFoundError:
        logger.warning(f"{command[0]} 检测失败: 在 PATH 中找不到 {command[0]}")
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"{command[0]} 检测失败: {type(e).__name__}: {e}")
    return None


class RecorderEngine:
    def __init__(
            self,
            paths: AppPaths | None = None,
            runner: ProcessRunner | None = None,
            platform_finder: Callable[[str], Platform | None] = find_platform,
            notifier: Notifier | None = None,
            watch_files: bool = True,
            detect_proxy: bool = True,
            background_interval: float = 3.0,
    ):
        ensure_tool_paths()
        self.paths = paths or AppPaths.default()
        self.bus = EventBus()
        self.config_store = ConfigStore(self.paths.config_file, self.paths.example_config_file)
        self.room_store = RoomStore(self.paths.url_config_file, is_supported_url)
        self.settings: Settings = self.config_store.load()
        self.runner = runner or ProcessRunner()
        self.find_platform = platform_finder
        self.notifier = notifier or Notifier(lambda: self.settings)
        self.watch_files = watch_files
        self.detect_proxy = detect_proxy
        self.background_interval = background_interval

        self._lock = threading.RLock()
        self._rooms: dict[str, Room] = {}
        self._workers: dict[str, RoomWorker] = {}
        self._index = 0
        self._shutdown = threading.Event()
        self._threads: list[threading.Thread] = []
        self._log_sink_id: int | None = None

        self.semaphore = AdjustableSemaphore(self.settings.max_request)
        self._preset_max_request = self.semaphore.limit
        self.error_count = 0
        self._error_window: list[int] = []
        self._error_lock = threading.Lock()

        self.global_proxy = False
        self.proxy_checked = False
        self.disk_full = False
        self.started = False
        self.started_at: float | None = None
        self.backup = BackupManager([self.paths.config_file, self.paths.url_config_file], self.paths.backup_dir)
        for room in self.room_store.load(self.settings.quality):
            self._rooms[room.id] = room

    # ================================================================== lifecycle

    def start(self, wait_proxy_check: bool = False) -> None:
        if self.started:
            return
        self.started = True
        self.started_at = time.time()
        self._shutdown.clear()
        self._log_sink_id = logger.add(self._loguru_sink, level='WARNING',
                                       filter=lambda r: not r['extra'].get('from_engine'))
        self.save_root.mkdir(parents=True, exist_ok=True)

        starter = threading.Thread(target=self._boot, daemon=True, name='engine-boot')
        starter.start()
        if wait_proxy_check:
            starter.join()
        for target, name in ((self._adjust_loop, 'engine-adjust'), (self._watch_loop, 'engine-watch'),
                             (self._backup_loop, 'engine-backup'), (self._disk_loop, 'engine-disk')):
            if name == 'engine-watch' and not self.watch_files:
                continue
            thread = threading.Thread(target=target, daemon=True, name=name)
            thread.start()
            self._threads.append(thread)

    def _boot(self) -> None:
        if self.detect_proxy:
            self.check_global_proxy()
        else:
            self.proxy_checked = True
        delay = max(0, int(self.settings.queue_delay))
        with self._lock:
            rooms = list(self._rooms.values())
        for room in rooms:
            if self._shutdown.is_set():
                return
            self._spawn(room)
            if delay and room.enabled:
                self._shutdown.wait(delay)

    def shutdown(self, timeout: float = 40) -> None:
        if not self.started:
            return
        self._shutdown.set()
        with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            worker.shutdown()
        deadline = time.time() + timeout
        for worker in workers:
            worker.join(timeout=max(0.1, deadline - time.time()))
        if self._log_sink_id is not None:
            try:
                logger.remove(self._log_sink_id)
            except ValueError:
                pass
            self._log_sink_id = None
        self.started = False

    # ================================================================== logging / errors

    def log(self, level: str, message: str, room_id: str | None = None) -> None:
        level = level if level in LOG_LEVELS else 'info'
        if level == 'warning':
            logger.bind(from_engine=True).warning(message)
        elif level == 'error':
            logger.bind(from_engine=True).error(message)
        self.bus.emit('log', level=level, message=message, room_id=room_id)

    def _loguru_sink(self, message) -> None:
        record = message.record
        self.bus.emit('log', level=record['level'].name.lower() if record['level'].name.lower() in LOG_LEVELS
                      else 'error', message=record['message'], room_id=None)

    def report_error(self) -> None:
        with self._error_lock:
            self.error_count += 1
            self._error_window.append(1)

    def _adjust_loop(self) -> None:
        while not self._shutdown.wait(5):
            with self._error_lock:
                window = self._error_window
                error_rate = sum(window) / len(window) if window else 0
                limit = self.semaphore.limit
                if error_rate > ERROR_THRESHOLD:
                    limit = max(1, limit - 1)
                elif error_rate < ERROR_THRESHOLD / 2 and limit < self._preset_max_request:
                    limit += 1
                if limit != self.semaphore.limit:
                    self.semaphore.set_limit(limit)
                    self.log('info', f"同一时间访问网络的线程数动态改为 {limit}")
                window.append(self.error_count)
                if len(window) > ERROR_WINDOW_SIZE:
                    window.pop(0)
                self.error_count = 0

    @contextmanager
    def request_slot(self, cancel: threading.Event | None = None):
        with self.semaphore.slot(cancel):
            yield

    # ================================================================== environment

    def check_global_proxy(self) -> bool:
        if self.settings.skip_proxy_check:
            self.global_proxy = True
        else:
            self.log('info', '系统代理检测中，请耐心等待...')
            try:
                urllib.request.urlopen("https://www.google.com/", timeout=15)
                self.global_proxy = True
                self.log('info', '全局/规则网络代理已开启√')
                try:
                    from src.proxy import ProxyDetector
                    detector = ProxyDetector()
                    if detector.is_proxy_enabled():
                        info = detector.get_proxy_info()
                        self.log('info', f"System Proxy: http://{info.ip}:{info.port}")
                except Exception:
                    pass
            except HTTPError as err:
                self.global_proxy = True
                self.log('info', f"HTTP error occurred: {err.code} - {err.reason}")
            except (URLError, OSError):
                self.global_proxy = False
                self.log('info', "未检测到全局/规则网络代理，请检查代理配置（若无需录制海外直播请忽略此条提示）")
        self.proxy_checked = True
        self.bus.emit('proxy_status', global_proxy=self.global_proxy)
        return self.global_proxy

    @property
    def save_root(self) -> Path:
        configured = (self.settings.save_path or '').strip()
        return Path(configured) if configured else self.paths.default_download_dir

    def disk_usage(self) -> tuple[float, float]:
        root = self.save_root
        probe = root if root.exists() else root.parent
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        usage = shutil.disk_usage(probe)
        return usage.free / (1024 ** 3), usage.total / (1024 ** 3)

    def _disk_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                free_gb, _ = self.disk_usage()
                limit = float(self.settings.disk_space_limit)
                if free_gb < limit and not self.disk_full:
                    self.disk_full = True
                    self.log('warning', f"磁盘剩余空间 {free_gb:.2f} GB 低于阈值 {limit} GB，已停止全部录制")
                    with self._lock:
                        workers = list(self._workers.values())
                    for worker in workers:
                        if worker.is_recording:
                            worker.stop_recording(skip_current_live=False)
                    self.bus.emit('disk_low', free_gb=free_gb, limit_gb=limit)
                elif free_gb >= limit and self.disk_full:
                    self.disk_full = False
                    self.log('info', f"磁盘剩余空间恢复到 {free_gb:.2f} GB，继续录制")
                    self.bus.emit('disk_ok', free_gb=free_gb)
            except Exception as e:
                self.log('error', f'检查磁盘空间失败: {e}')
            self._shutdown.wait(10)

    def _backup_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                self.backup.run_once()
            except Exception as e:
                self.log('error', f"备份配置文件失败, 错误信息: {e}")
            self._shutdown.wait(600)

    def _watch_loop(self) -> None:
        while not self._shutdown.wait(self.background_interval):
            try:
                if self.config_store.changed_on_disk():
                    self._apply_settings(self.config_store.load())
                    self.log('info', '检测到 config.ini 变更，配置已重新加载')
                if self.room_store.changed_on_disk():
                    self.sync_rooms_from_file()
            except Exception as e:
                self.log('error', f'重新加载配置失败: {e}')

    def system_info(self) -> dict[str, Any]:
        try:
            free_gb, total_gb = self.disk_usage()
        except OSError:
            free_gb = total_gb = 0.0
        statuses = self.snapshot()
        return {
            'version': VERSION,
            'started_at': self.started_at,
            'ffmpeg': tool_version(['ffmpeg', '-version']),
            'node': tool_version(['node', '-v']),
            'global_proxy': self.global_proxy,
            'proxy_checked': self.proxy_checked,
            'save_root': str(self.save_root),
            'disk_free_gb': round(free_gb, 2),
            'disk_total_gb': round(total_gb, 2),
            'disk_full': self.disk_full,
            'max_request': self.semaphore.limit,
            'error_count': self.error_count,
            'rooms_total': len(statuses),
            'rooms_monitoring': sum(1 for s in statuses if s['enabled']),
            'rooms_recording': sum(1 for s in statuses if s['state'] == RoomState.RECORDING.value),
        }

    # ================================================================== settings

    def _apply_settings(self, settings: Settings) -> None:
        old = self.settings
        self.settings = settings
        if settings.max_request != old.max_request:
            self._preset_max_request = max(1, int(settings.max_request))
            self.semaphore.set_limit(self._preset_max_request)
        self.bus.emit('settings_changed', settings=settings.to_dict())

    def update_settings(self, values: dict[str, Any] | None = None,
                        cookies: dict[str, str] | None = None) -> Settings:
        settings = self.config_store.save(values, cookies)
        self._apply_settings(settings)
        return settings

    def apply_config_updates(self, updates: list[tuple[str, str, str]]) -> None:
        for section, key, value in updates:
            self.config_store.set_raw(section, key, value)
        self._apply_settings(self.config_store.load())

    def push_async(self, live_url: str, content: str, record_name: str) -> None:
        def run():
            for channel, result in self.notifier.send(live_url, content).items():
                self.log('info', f'提示信息：已经将[{record_name}]直播状态消息推送至你的{channel}, {result}')

        threading.Thread(target=run, daemon=True, name='push').start()

    def test_notify(self, channels: list[str] | None = None) -> dict[str, str]:
        return self.notifier.send('https://github.com/ihmily/DouyinLiveRecorder',
                                  '这是一条来自 DouyinLiveRecorder 的测试推送', channels)

    # ================================================================== rooms

    def _spawn(self, room: Room) -> RoomWorker:
        with self._lock:
            existing = self._workers.get(room.id)
            if existing and existing.is_alive():
                return existing
            self._index += 1
            worker = RoomWorker(self, room, self._index)
            self._workers[room.id] = worker
        worker.start()
        return worker

    def _persist_rooms(self) -> None:
        # Lock order is always room_store.lock -> self._lock.
        with self.room_store.lock:
            if self.watch_files and self.room_store.changed_on_disk():
                self.sync_rooms_from_file()
            with self._lock:
                rooms = list(self._rooms.values())
            self.room_store.save(rooms)

    def rooms(self) -> list[Room]:
        with self._lock:
            return [room.copy() for room in self._rooms.values()]

    def get_room(self, room_id: str) -> Room:
        with self._lock:
            if room_id not in self._rooms:
                raise KeyError(f'直播间不存在: {room_id}')
            return self._rooms[room_id]

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [(room, self._workers.get(room_id)) for room_id, room in self._rooms.items()]
        result = []
        for index, (room, worker) in enumerate(items, start=1):
            if worker:
                result.append(worker.status.to_dict())
            else:
                from .worker import RoomStatus
                status = RoomStatus(id=room.id, url=room.url, quality=room.quality, name=room.name,
                                    enabled=room.enabled, index=index,
                                    state=RoomState.IDLE if room.enabled else RoomState.PAUSED)
                platform = self.find_platform(room.url)
                status.platform = platform.name if platform else ''
                result.append(status.to_dict())
        return result

    def add_room(self, url: str, quality: str | None = None, name: str = '', enabled: bool = True) -> Room:
        url = normalize_url(url)
        if not is_supported_url(url):
            raise ValueError(f'不支持的直播间地址: {url}')
        with self._lock:
            if any(r.url == url for r in self._rooms.values()):
                raise ValueError(f'直播间已存在: {url}')
            room = Room(url=url, quality=normalize_quality(quality or self.settings.quality),
                        name=(name or '').strip(), enabled=enabled)
            self._rooms[room.id] = room
        self._persist_rooms()
        self.bus.emit('room_added', room_id=room.id)
        if self.started:
            self._spawn(room)
        return room

    def add_rooms_text(self, text: str, quality: str | None = None) -> tuple[list[Room], list[str]]:
        """Bulk import; accepts the same line format as URL_config.ini."""
        added, skipped = [], []
        default_quality = normalize_quality(quality or self.settings.quality)
        for line in text.splitlines():
            if not line.strip():
                continue
            parsed = parse_line(line, default_quality, is_supported_url)
            if not parsed.room:
                skipped.append(line.strip())
                continue
            try:
                added.append(self.add_room(parsed.room.url, parsed.room.quality, parsed.room.name,
                                           parsed.room.enabled))
            except ValueError as e:
                skipped.append(f'{line.strip()} ({e})')
        return added, skipped

    def update_room(self, room_id: str, *, url: str | None = None, quality: str | None = None,
                    name: str | None = None, enabled: bool | None = None) -> Room:
        with self._lock:
            room = self.get_room(room_id)
            if url is not None:
                url = normalize_url(url)
                if not is_supported_url(url):
                    raise ValueError(f'不支持的直播间地址: {url}')
                if any(r.url == url and r.id != room_id for r in self._rooms.values()):
                    raise ValueError(f'直播间已存在: {url}')
                room.url = url
            if quality is not None:
                room.quality = normalize_quality(quality)
            if name is not None:
                room.name = name.strip()
            if enabled is not None:
                room.enabled = enabled
            worker = self._workers.get(room_id)
        self._persist_rooms()
        if worker:
            worker.sync_room(room)
        return room

    def remove_room(self, room_id: str) -> None:
        with self._lock:
            self.get_room(room_id)
            self._rooms.pop(room_id)
            worker = self._workers.pop(room_id, None)
        self._persist_rooms()
        if worker:
            worker.shutdown()
        self.bus.emit('room_removed', room_id=room_id)

    def pause_room(self, room_id: str) -> Room:
        return self.update_room(room_id, enabled=False)

    def resume_room(self, room_id: str) -> Room:
        room = self.update_room(room_id, enabled=True)
        if self.started and room_id not in self._workers:
            self._spawn(room)
        return room

    def pause_all(self) -> None:
        for room in self.rooms():
            if room.enabled:
                self.pause_room(room.id)

    def resume_all(self) -> None:
        for room in self.rooms():
            if not room.enabled:
                self.resume_room(room.id)

    def check_now(self, room_id: str) -> None:
        self.get_room(room_id)
        worker = self._workers.get(room_id)
        if worker:
            worker.wake()

    def stop_recording(self, room_id: str) -> None:
        """Stop the current recording and skip this broadcast; monitoring continues."""
        self.get_room(room_id)
        worker = self._workers.get(room_id)
        if worker:
            worker.stop_recording(skip_current_live=True)

    def set_room_name(self, room_id: str, name: str) -> None:
        with self._lock:
            room = self._rooms.get(room_id)
            if not room or room.name == name:
                return
            room.name = name
        self._persist_rooms()

    def update_room_url(self, room_id: str, new_url: str) -> None:
        with self._lock:
            room = self._rooms.get(room_id)
            if not room or any(r.url == new_url for r in self._rooms.values()):
                return
            room.url = new_url
            worker = self._workers.get(room_id)
        self._persist_rooms()
        if worker:
            worker.status.url = new_url

    def sync_rooms_from_file(self) -> None:
        """Apply external edits of URL_config.ini (CLI workflow: add lines, prefix '#' to pause)."""
        with self.room_store.lock:
            self._sync_rooms_from_file()

    def _sync_rooms_from_file(self) -> None:
        file_rooms = self.room_store.load(self.settings.quality)
        with self._lock:
            by_url = {room.url: room for room in self._rooms.values()}
        seen = set()
        for file_room in file_rooms:
            seen.add(file_room.url)
            existing = by_url.get(file_room.url)
            if existing is None:
                with self._lock:
                    self._rooms[file_room.id] = file_room
                self.log('info', f"新增地址: {file_room.url}")
                self.bus.emit('room_added', room_id=file_room.id)
                if self.started:
                    self._spawn(file_room)
                continue
            if (existing.quality, existing.name, existing.enabled) != (
                    file_room.quality, file_room.name or existing.name, file_room.enabled):
                with self._lock:
                    existing.quality = file_room.quality
                    existing.name = file_room.name or existing.name
                    if existing.enabled != file_room.enabled:
                        existing.enabled = file_room.enabled
                        self.log('info', f"[{existing.name or existing.url}] "
                                         f"{'已恢复监测' if existing.enabled else '已被注释,停止监测'}")
                    worker = self._workers.get(existing.id)
                if worker:
                    worker.sync_room(existing)
        for url, room in by_url.items():
            if url not in seen:
                with self._lock:
                    self._rooms.pop(room.id, None)
                    worker = self._workers.pop(room.id, None)
                if worker:
                    worker.shutdown()
                self.log('info', f"[{room.name or room.url}]已经从录制列表中移除")
                self.bus.emit('room_removed', room_id=room.id)

    # ================================================================== files

    def _resolve_inside_root(self, path: str | Path) -> Path:
        root = self.save_root.resolve()
        target = (root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        if root != target and root not in target.parents:
            raise PermissionError('只能操作录制目录内的文件')
        return target

    def list_recordings(self, limit: int = 2000) -> list[dict[str, Any]]:
        root = self.save_root
        if not root.exists():
            return []
        items = []
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                path = Path(dirpath) / filename
                try:
                    stat = path.stat()
                except OSError:
                    continue
                items.append({
                    'path': str(path),
                    'relative': path.relative_to(root).as_posix(),
                    'name': filename,
                    'size': stat.st_size,
                    'mtime': stat.st_mtime,
                })
        items.sort(key=lambda item: item['mtime'], reverse=True)
        return items[:limit]

    def resolve_recording(self, path: str | Path) -> Path:
        target = self._resolve_inside_root(path)
        if not target.is_file():
            raise FileNotFoundError(str(path))
        return target

    def delete_recording(self, path: str | Path) -> None:
        target = self.resolve_recording(path)
        recording_files = {s['record_file'] for s in self.snapshot() if s['state'] == RoomState.RECORDING.value}
        if str(target).replace('\\', '/') in {f.replace('\\', '/') for f in recording_files}:
            raise PermissionError('文件正在录制中，不能删除')
        target.unlink()

    def convert_recording(self, path: str | Path) -> None:
        target = self.resolve_recording(path)
        convert_files_async([target], self.settings.convert_to_h264, False,
                            on_done=lambda src, dst: self.bus.emit('file_converted', source=str(src),
                                                                   target=str(dst) if dst else None))
        self.log('info', f'开始转换为 mp4: {target.name}')
