# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import datetime
import os
import random
import threading
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import TYPE_CHECKING

from src.logger import logger
from src.utils import get_query_params

from .common import clean_name, quality_code
from .ffmpeg import RecordPlan, build_save_dir, plan_recording
from .platforms import FetchContext, Platform, PlatformError, fetch_stream, is_overseas_url, resolve_proxy
from .postprocess import SubtitleWriter, build_script_command, convert_files_async, run_script
from .rooms import Room

if TYPE_CHECKING:
    from .engine import RecorderEngine


class RoomState(str, Enum):
    IDLE = 'idle'
    CHECKING = 'checking'
    WAITING = 'waiting'
    LIVE = 'live'
    RECORDING = 'recording'
    PAUSED = 'paused'
    ERROR = 'error'
    STOPPED = 'stopped'


STATE_LABELS = {
    RoomState.IDLE: '待检测',
    RoomState.CHECKING: '检测中',
    RoomState.WAITING: '未开播',
    RoomState.LIVE: '直播中(未录制)',
    RoomState.RECORDING: '录制中',
    RoomState.PAUSED: '已暂停',
    RoomState.ERROR: '出错',
    RoomState.STOPPED: '已停止',
}

QUICK_RECHECK_SECONDS = 30
STREAM_URL_LOG_PLATFORMS = ('WinkTV', 'PandaTV', 'ShowRoom', 'CHZZK', 'Youtube')


@dataclass
class RoomStatus:
    id: str
    url: str
    quality: str
    name: str
    enabled: bool
    index: int
    platform: str = ''
    state: RoomState = RoomState.IDLE
    title: str = ''
    is_live: bool = False
    stream_url: str = ''
    record_file: str = ''
    record_started_at: float | None = None
    last_check: float | None = None
    next_check: float | None = None
    last_error: str = ''
    skipped_live: bool = False

    def to_dict(self) -> dict:
        data = asdict(self)
        data['state'] = self.state.value
        data['state_label'] = STATE_LABELS[self.state]
        data['duration'] = int(time.time() - self.record_started_at) if self.record_started_at else 0
        return data


def select_source_url(platform: Platform, info: dict) -> str | None:
    if platform.flv_preferred and info.get('flv_url'):
        codec = get_query_params(info['flv_url'], "codec")
        if not (codec and codec[0] == 'h265'):
            return info['flv_url']
    return info.get('record_url')


def is_h265_flv(platform: Platform, info: dict) -> bool:
    if not (platform.flv_preferred and info.get('flv_url')):
        return False
    codec = get_query_params(info['flv_url'], "codec")
    return bool(codec and codec[0] == 'h265')


class RoomWorker(threading.Thread):
    def __init__(self, engine: "RecorderEngine", room: Room, index: int):
        super().__init__(daemon=True, name=f'room-{index}')
        self.engine = engine
        self.room = room
        self.status = RoomStatus(id=room.id, url=room.url, quality=room.quality, name=room.name,
                                 enabled=room.enabled, index=index)
        self._wake = threading.Event()
        self._exit = threading.Event()
        self._stop_recording = threading.Event()
        self._start_pushed = False

    # ------------------------------------------------------------------ control

    def wake(self) -> None:
        self._wake.set()

    def stop_recording(self, skip_current_live: bool = True) -> None:
        if skip_current_live:
            self.status.skipped_live = True
        self._stop_recording.set()
        self._wake.set()

    def shutdown(self) -> None:
        self._exit.set()
        self._stop_recording.set()
        self._wake.set()

    @property
    def is_recording(self) -> bool:
        return self.status.state == RoomState.RECORDING

    @property
    def record_name(self) -> str:
        return f'序号{self.status.index} {self.status.name or self.room.url}'

    def sync_room(self, room: Room) -> None:
        self.room = room
        self.status.url, self.status.quality = room.url, room.quality
        self.status.name, self.status.enabled = room.name, room.enabled
        if not room.enabled:
            self._stop_recording.set()
        self._wake.set()
        self._publish()

    # ------------------------------------------------------------------ helpers

    def _publish(self) -> None:
        self.engine.bus.emit('room_state', room=self.status.to_dict())

    def _set(self, state: RoomState | None = None, **fields) -> None:
        if state is not None:
            self.status.state = state
        for key, value in fields.items():
            setattr(self.status, key, value)
        self._publish()

    def _log(self, level: str, message: str) -> None:
        self.engine.log(level, message, room_id=self.room.id)

    def _sleep(self, seconds: float) -> None:
        self.status.next_check = time.time() + seconds
        self._publish()
        self._wake.wait(max(0.0, seconds))
        self._wake.clear()
        self.status.next_check = None

    def _interval(self) -> int:
        delay = max(0, random.randint(-5, 5) + int(self.engine.settings.loop_interval))
        if self.engine.error_count > 20:
            delay += 60
            self._log('debug', '瞬时错误太多,延迟加60秒')
        return delay

    # ------------------------------------------------------------------ main loop

    def run(self) -> None:
        while not self._exit.is_set():
            if not self.room.enabled:
                self._set(RoomState.PAUSED, is_live=False, next_check=None)
                self._wake.wait()
                self._wake.clear()
                continue
            try:
                delay = self.check_once()
            except Exception as e:
                logger.exception(e)
                self.engine.report_error()
                self._set(RoomState.ERROR, last_error=str(e))
                delay = self._interval()
            if self._exit.is_set():
                break
            self._sleep(delay)
        self._set(RoomState.STOPPED, is_live=False, next_check=None)

    def check_once(self) -> float:
        engine = self.engine
        settings = engine.settings
        room = self.room
        platform = engine.find_platform(room.url)
        if platform is None:
            self._set(RoomState.ERROR, last_error='不支持的直播间地址')
            return max(60, settings.loop_interval)

        proxy = resolve_proxy(room.url, settings)
        ctx = FetchContext(url=room.url, quality=quality_code(room.quality), proxy=proxy, settings=settings,
                           global_proxy=engine.global_proxy, platform=platform)
        self._set(RoomState.CHECKING, platform=platform.name, last_check=time.time())
        try:
            with engine.request_slot(self._exit):
                if self._exit.is_set():
                    return 0
                result = asyncio.run(fetch_stream(platform, ctx))
        except PlatformError as e:
            self._log('error', f'{self.record_name} {e}')
            self._set(RoomState.ERROR, last_error=str(e))
            return self._interval()
        except Exception as e:
            self.engine.report_error()
            self._log('error', f'{self.record_name} 获取直播信息失败: {type(e).__name__}: {e}')
            self._set(RoomState.ERROR, last_error=f'{type(e).__name__}: {e}')
            return self._interval()

        if result.config_updates:
            engine.apply_config_updates(result.config_updates)

        info = result.info
        if not info.get('anchor_name'):
            self.engine.report_error()
            self._log('warning', f'序号{self.status.index} 网址内容获取失败,进行重试中...获取失败的地址是:{room.url}')
            self._set(RoomState.ERROR, last_error='直播间信息获取失败')
            return self._interval()

        anchor_name = clean_name(room.name or info['anchor_name'], settings.clean_emoji)
        if not room.name:
            engine.set_room_name(room.id, anchor_name)
        if result.new_url and result.new_url != room.url:
            engine.update_room_url(room.id, result.new_url)

        self.status.name = anchor_name
        title = info.get('title') or ''

        if not info.get('is_live'):
            self._set(RoomState.WAITING, is_live=False, title=title, last_error='', skipped_live=False,
                      stream_url='')
            self._log('debug', f'{self.record_name} 等待直播... ')
            if self._start_pushed:
                if settings.over_push:
                    engine.push_async(room.url, engine.notifier.over_text(self.record_name), self.record_name)
                self._start_pushed = False
            return self._interval()

        self._log('debug', f'{self.record_name} 正在直播中...')
        if settings.push_channels and not self._start_pushed:
            if settings.begin_push:
                engine.push_async(room.url, engine.notifier.begin_text(self.record_name), self.record_name)
            self._start_pushed = True

        if settings.disable_record:
            self._set(RoomState.LIVE, is_live=True, title=title, last_error='')
            return settings.push_check_seconds
        if self.status.skipped_live:
            self._set(RoomState.LIVE, is_live=True, title=title, last_error='已跳过本场直播')
            return self._interval()
        if engine.disk_full:
            self._set(RoomState.LIVE, is_live=True, title=title, last_error='磁盘剩余空间不足，暂停录制')
            return self._interval()

        finished = self.record(platform, info, anchor_name, proxy)
        return QUICK_RECHECK_SECONDS if finished else self._interval()

    # ------------------------------------------------------------------ recording

    def record(self, platform: Platform, info: dict, anchor_name: str, proxy: str | None) -> bool:
        """Records until the stream ends or a stop is requested. Returns True when the stream ended by itself."""
        engine = self.engine
        settings = engine.settings
        real_url = select_source_url(platform, info)
        if not real_url:
            self._set(RoomState.LIVE, is_live=True, last_error='未获取到直播源地址')
            return False
        if is_h265_flv(platform, info):
            self._log('warning', "FLV is not supported for h265 codec, use HLS source instead")

        now = datetime.datetime.today().strftime("%Y-%m-%d_%H-%M-%S")
        title = info.get('title') or ''
        clean_title = clean_name(title, settings.clean_emoji) if title else ''
        title_in_name = clean_title + '_' if settings.filename_by_title and clean_title else ''

        full_path = build_save_dir(str(engine.save_root), platform.name, anchor_name, now[:10], clean_title,
                                   settings.folder_by_author, settings.folder_by_time, settings.folder_by_title)
        os.makedirs(full_path, exist_ok=True)

        if not platform.custom:
            if settings.force_https and real_url.startswith("http://"):
                real_url = real_url.replace("http://", "https://")
            if platform.force_http:
                real_url = real_url.replace("https://", "http://")

        save_type = settings.save_format_code
        if is_h265_flv(platform, info):
            save_type = 'TS'

        if settings.show_url:
            shown = info.get('m3u8_url') if platform.name in STREAM_URL_LOG_PLATFORMS else real_url
            logger.info(f"{platform.name} | {anchor_name} | 直播源地址: {shown}")

        direct_flv = None
        if platform.only_flv:
            direct_flv = info.get('flv_url')
            if not direct_flv:
                self._log('debug', f'{self.record_name} 未找到FLV直播流，跳过录制')
                self._set(RoomState.LIVE, is_live=True, last_error='未找到FLV直播流')
                return False
            self._log('debug', f'提示: {platform.name} 将强制使用FLV格式录制')

        base_path = f'{full_path}/{anchor_name}_{title_in_name}{now}'
        plan = plan_recording(
            real_url, base_path, save_type,
            split=settings.split_enabled, split_time=settings.split_time,
            proxy=proxy, header=platform.header_for(self.room.url), overseas=is_overseas_url(self.room.url),
            audio_only=platform.only_audio, direct_flv_url=direct_flv,
        )
        return self._execute(plan, platform, base_path, title, real_url)

    def _execute(self, plan: RecordPlan, platform: Platform, base_path: str, title: str, real_url: str) -> bool:
        engine = self.engine
        settings = engine.settings
        if self._exit.is_set() or not self.room.enabled or self.status.skipped_live:
            return False
        self._stop_recording.clear()

        subtitles = None
        if settings.create_time_file and not plan.segmented and not plan.is_audio:
            subtitles = SubtitleWriter(base_path)
            subtitles.start()

        started_at = time.time()
        self._set(RoomState.RECORDING, is_live=True, title=title, stream_url=real_url,
                  record_file=plan.output_path, record_started_at=started_at, last_error='')
        self._log('info', f'{self.record_name} 开始录制: {plan.output_path}')
        engine.bus.emit('recording_started', room=self.status.to_dict(), file=plan.output_path)

        return_code, error = 1, ''
        try:
            if plan.is_direct:
                headers = {}
                header = platform.header_for(self.room.url)
                if header:
                    key, value = header.split(":", 1)
                    headers[key] = value
                ok = engine.runner.download(plan.direct_url, plan.output_path, headers, self._stop_recording)
                return_code = 0 if ok else 1
            else:
                return_code = engine.runner.run(
                    plan.command, self._stop_recording,
                    on_line=lambda line: self._log('warning', f'{self.record_name} ffmpeg: {line}'))
        except Exception as e:
            error = f'{type(e).__name__}: {e}'
        finally:
            if subtitles:
                subtitles.stop()

        stopped = self._stop_recording.is_set()
        files = plan.produced_files()
        stop_time = time.strftime('%Y-%m-%d %H:%M:%S')

        if (return_code == 0 or stopped) and settings.convert_to_mp4 and plan.save_type in ('TS', 'FLV') and files:
            suffix = '并重新编码为h264' if settings.convert_to_h264 else ''
            self._log('info', f'{self.record_name} 正在转码为MP4格式{suffix}')
            convert_files_async(files, settings.convert_to_h264, settings.delete_origin,
                                on_done=lambda src, dst: engine.bus.emit('file_converted', source=str(src),
                                                                         target=str(dst) if dst else None))

        if stopped:
            self._log('info', f'{self.record_name} {stop_time} 录制已停止，文件已保存')
        elif return_code == 0:
            self._log('info', f'{self.record_name} {stop_time} 直播录制完成')
            if settings.run_script and settings.script_command:
                self._log('debug', '开始执行脚本命令!')
                command = build_script_command(settings.script_command, self.record_name, plan.output_path,
                                               plan.save_type, settings.split_enabled, settings.convert_to_mp4)
                run_script(command, log=self._log)
                self._log('debug', '脚本命令执行结束!')
        else:
            engine.report_error()
            error = error or f'返回码: {return_code}'
            self._log('error', f'{self.record_name} {stop_time} 直播录制出错, {error}')

        engine.bus.emit('recording_finished', room=self.status.to_dict(), files=[str(f) for f in files],
                        return_code=return_code, stopped=stopped, error=error)
        state = RoomState.PAUSED if not self.room.enabled else RoomState.LIVE if stopped else RoomState.WAITING
        self._set(state, record_started_at=None, last_error=error, is_live=stopped and self.room.enabled)
        return not stopped and return_code == 0
