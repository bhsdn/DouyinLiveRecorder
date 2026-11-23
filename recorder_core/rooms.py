# -*- coding: utf-8 -*-
"""Reads and writes config/URL_config.ini.

Line format (unchanged from the original program):
    [#][画质,]直播间地址[,主播: 名称]
A leading '#' pauses the room.
"""
from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from .common import QUALITY_CHOICES, TEXT_ENCODING, atomic_write_text

ANCHOR_PREFIX = '主播:'
MIN_LINE_LENGTH = 18

CLEAN_QUERY_HOSTS = (
    "live.douyin.com",
    "live.bilibili.com",
    "www.huajiao.com",
    "www.zhihu.com",
    "www.huya.com",
    "chzzk.naver.com",
    "www.liveme.com",
    "www.haixiutv.com",
    "v.6.cn",
    "m.6.cn",
    'www.lehaitv.com',
)

_URL_PATTERN = re.compile(r"(https?://)?(www\.)?[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+(:\d+)?(/.*)?")


def new_room_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Room:
    url: str
    quality: str = '原画'
    name: str = ''
    enabled: bool = True
    id: str = field(default_factory=new_room_id)

    def to_line(self) -> str:
        line = f'{self.quality},{self.url}'
        if self.name:
            line += f',{ANCHOR_PREFIX} {self.name}'
        return line if self.enabled else '#' + line

    def copy(self, **changes) -> "Room":
        return replace(self, **changes)


def contains_url(text: str) -> bool:
    return _URL_PATTERN.search(text) is not None


def url_host(url: str) -> str:
    parts = url.split('/')
    return parts[2] if len(parts) > 2 else ''


def normalize_url(url: str) -> str:
    url = url.strip()
    if '://' not in url:
        url = 'https://' + url
    host = url_host(url)
    if host in CLEAN_QUERY_HOSTS:
        url = url.split('?')[0]
    if 'xiaohongshu' in url:
        host_id = re.search('&host_id=(.*?)(?=&|$)', url)
        if host_id:
            url = url.split('?')[0] + f'?host_id={host_id.group(1)}'
    return url


def normalize_quality(quality: str, default: str = '原画') -> str:
    quality = (quality or '').strip()
    if quality in QUALITY_CHOICES:
        return quality
    return default if default in QUALITY_CHOICES else '原画'


def parse_anchor(text: str) -> str:
    text = (text or '').strip()
    if ANCHOR_PREFIX in text:
        text = text.split(ANCHOR_PREFIX)[-1]
    return text.strip()


@dataclass
class ParsedLine:
    room: Room | None
    raw: str
    unsupported: bool = False


def parse_line(line: str, default_quality: str, is_supported: Callable[[str], bool]) -> ParsedLine:
    raw = line.rstrip('\r\n')
    text = raw.strip()
    if len(text) < MIN_LINE_LENGTH:
        return ParsedLine(None, raw)

    enabled = not text.startswith('#')
    if not enabled:
        text = text.lstrip('#').strip()

    parts = [p.strip() for p in re.split('[,，]', text)]
    if len(parts) == 1:
        quality, url, name = default_quality, parts[0], ''
    elif len(parts) == 2:
        if contains_url(parts[0]):
            quality, (url, name) = default_quality, parts
        else:
            (quality, url), name = parts, ''
    else:
        quality, url, name = parts[0], parts[1], ','.join(parts[2:])

    if not url:
        return ParsedLine(None, raw)

    url = normalize_url(url)
    if not is_supported(url):
        return ParsedLine(None, raw, unsupported=enabled)

    room = Room(url=url, quality=normalize_quality(quality, '原画'), name=parse_anchor(name), enabled=enabled)
    return ParsedLine(room, raw)


class RoomStore:
    def __init__(self, path: Path, is_supported: Callable[[str], bool]):
        self.path = Path(path)
        self.is_supported = is_supported
        self._lock = threading.RLock()
        self._mtime: float | None = None
        self._extra_lines: list[str] = []

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def _remember_mtime(self) -> None:
        try:
            self._mtime = self.path.stat().st_mtime_ns
        except OSError:
            self._mtime = None

    def changed_on_disk(self) -> bool:
        try:
            return self.path.stat().st_mtime_ns != self._mtime
        except OSError:
            return False

    def is_empty(self) -> bool:
        if not self.path.exists():
            return True
        return not self.path.read_text(encoding=TEXT_ENCODING, errors='ignore').strip()

    def load(self, default_quality: str = '原画') -> list[Room]:
        with self._lock:
            if not self.path.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text('', encoding=TEXT_ENCODING)
            original = self.path.read_text(encoding=TEXT_ENCODING, errors='ignore')

            rooms: list[Room] = []
            extra: list[str] = []
            seen_urls: set[str] = set()
            for line in original.splitlines():
                parsed = parse_line(line, default_quality, self.is_supported)
                if parsed.room is None:
                    if parsed.unsupported:
                        extra.append('#' + parsed.raw.strip())
                    elif parsed.raw.strip() and parsed.raw not in extra:
                        extra.append(parsed.raw)
                    continue
                if parsed.room.url in seen_urls:
                    continue
                seen_urls.add(parsed.room.url)
                rooms.append(parsed.room)

            self._extra_lines = extra
            if self._render(rooms) != self._normalized(original):
                self._write(rooms)
            else:
                self._remember_mtime()
            return rooms

    @staticmethod
    def _normalized(text: str) -> str:
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        return '\n'.join(lines) + ('\n' if lines else '')

    def _render(self, rooms: list[Room]) -> str:
        lines = list(self._extra_lines) + [room.to_line() for room in rooms]
        return '\n'.join(lines) + ('\n' if lines else '')

    def _write(self, rooms: list[Room]) -> None:
        atomic_write_text(self.path, self._render(rooms))
        self._remember_mtime()

    def save(self, rooms: list[Room]) -> None:
        with self._lock:
            self._write(rooms)
