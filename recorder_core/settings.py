# -*- coding: utf-8 -*-
"""Typed access to config/config.ini.

Section and key names are kept exactly as the original program wrote them so that
existing config files keep working. Writes go through IniDocument, which edits lines
in place and therefore keeps the user's comments and ordering.
"""
from __future__ import annotations

import copy
import hashlib
import re
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .common import QUALITY_CHOICES, TEXT_ENCODING, atomic_write_text

SECTION_RECORD = '录制设置'
SECTION_PUSH = '推送配置'
SECTION_COOKIE = 'Cookie'
SECTION_AUTH = 'Authorization'
SECTION_ACCOUNT = '账号密码'
SECTION_WEB = 'Web'

SAVE_FORMATS = ('ts', 'mkv', 'flv', 'mp4', 'mp3音频', 'm4a音频')
PUSH_CHANNELS = ('微信', '钉钉', 'TG', '邮箱', 'BARK', 'NTFY', 'PUSHPLUS')

TRUE_WORDS = {'是', 'true', '1', 'yes', 'y', 'on'}
FALSE_WORDS = {'否', 'false', '0', 'no', 'n', 'off'}


@dataclass(frozen=True)
class FieldSpec:
    name: str
    section: str
    key: str
    default: Any
    kind: str
    label: str
    group: str
    choices: tuple = ()
    help: str = ''


GROUP_RECORD = '录制'
GROUP_FILES = '文件与后处理'
GROUP_PROXY = '代理'
GROUP_PUSH = '推送'
GROUP_ACCOUNT = '账号'
GROUP_WEB = 'Web'
GROUP_UI = '界面'

DEFAULT_PROXY_PLATFORMS = ('tiktok, soop, pandalive, winktv, flextv, popkontv, twitch, liveme, showroom, chzzk, '
                           'shopee, shp, youtu, faceit')

SPECS: tuple[FieldSpec, ...] = (
    FieldSpec('language', SECTION_RECORD, 'language(zh_cn/en)', 'zh_cn', 'choice', '命令行语言', GROUP_UI,
              ('zh_cn', 'en'), '控制命令行模式下平台模块输出的翻译'),
    FieldSpec('skip_proxy_check', SECTION_RECORD, '是否跳过代理检测(是/否)', False, 'bool', '跳过启动时的代理检测',
              GROUP_PROXY),
    FieldSpec('save_path', SECTION_RECORD, '直播保存路径(不填则默认)', '', 'path', '保存路径', GROUP_FILES,
              help='留空则保存到程序目录下的 downloads'),
    FieldSpec('folder_by_author', SECTION_RECORD, '保存文件夹是否以作者区分', True, 'bool', '按主播分文件夹',
              GROUP_FILES),
    FieldSpec('folder_by_time', SECTION_RECORD, '保存文件夹是否以时间区分', False, 'bool', '按日期分文件夹',
              GROUP_FILES),
    FieldSpec('folder_by_title', SECTION_RECORD, '保存文件夹是否以标题区分', False, 'bool', '按直播标题分文件夹',
              GROUP_FILES),
    FieldSpec('filename_by_title', SECTION_RECORD, '保存文件名是否包含标题', False, 'bool', '文件名包含标题',
              GROUP_FILES),
    FieldSpec('clean_emoji', SECTION_RECORD, '是否去除名称中的表情符号', True, 'bool', '去除名称中的表情符号',
              GROUP_FILES),
    FieldSpec('save_format', SECTION_RECORD, '视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频', 'ts', 'choice', '保存格式',
              GROUP_RECORD, SAVE_FORMATS, '推荐 ts，断流时文件不易损坏'),
    FieldSpec('quality', SECTION_RECORD, '原画|超清|高清|标清|流畅', '原画', 'choice', '默认画质', GROUP_RECORD,
              QUALITY_CHOICES),
    FieldSpec('use_proxy', SECTION_RECORD, '是否使用代理ip(是/否)', True, 'bool', '使用代理', GROUP_PROXY),
    FieldSpec('proxy_addr', SECTION_RECORD, '代理地址', '', 'str', '代理地址', GROUP_PROXY,
              help='例如 127.0.0.1:7890'),
    FieldSpec('max_request', SECTION_RECORD, '同一时间访问网络的线程数', 3, 'int', '同时检测的直播间数', GROUP_RECORD,
              help='请求过于频繁可能被平台封禁 IP'),
    FieldSpec('loop_interval', SECTION_RECORD, '循环时间(秒)', 120, 'int', '检测间隔(秒)', GROUP_RECORD),
    FieldSpec('queue_delay', SECTION_RECORD, '排队读取网址时间(秒)', 0, 'int', '启动各直播间的间隔(秒)',
              GROUP_RECORD),
    FieldSpec('show_loop_time', SECTION_RECORD, '是否显示循环秒数', False, 'bool', '显示循环倒计时(命令行)',
              GROUP_UI),
    FieldSpec('show_url', SECTION_RECORD, '是否显示直播源地址', False, 'bool', '记录直播源地址到 PlayURL.log',
              GROUP_RECORD),
    FieldSpec('split_enabled', SECTION_RECORD, '分段录制是否开启', False, 'bool', '分段录制', GROUP_RECORD),
    FieldSpec('force_https', SECTION_RECORD, '是否强制启用https录制', False, 'bool', '强制 https 拉流',
              GROUP_RECORD),
    FieldSpec('disk_space_limit', SECTION_RECORD, '录制空间剩余阈值(gb)', 1.0, 'float', '磁盘剩余空间阈值(GB)',
              GROUP_RECORD, help='低于该值时停止录制'),
    FieldSpec('split_time', SECTION_RECORD, '视频分段时间(秒)', 1800, 'int', '分段时长(秒)', GROUP_RECORD),
    FieldSpec('convert_to_mp4', SECTION_RECORD, '录制完成后自动转为mp4格式', False, 'bool', '录完自动转 mp4',
              GROUP_FILES, help='对 ts / flv 格式生效'),
    FieldSpec('convert_to_h264', SECTION_RECORD, 'mp4格式重新编码为h264', False, 'bool', '转 mp4 时重编码为 h264',
              GROUP_FILES, help='耗 CPU，兼容性更好'),
    FieldSpec('delete_origin', SECTION_RECORD, '追加格式后删除原文件', False, 'bool', '转换后删除原文件',
              GROUP_FILES),
    FieldSpec('create_time_file', SECTION_RECORD, '生成时间字幕文件', False, 'bool', '生成时间字幕(srt)',
              GROUP_FILES),
    FieldSpec('run_script', SECTION_RECORD, '是否录制完成后执行自定义脚本', False, 'bool', '录完执行自定义脚本',
              GROUP_FILES),
    FieldSpec('script_command', SECTION_RECORD, '自定义脚本执行命令', '', 'str', '自定义脚本命令', GROUP_FILES,
              help='例如 python D:/scripts/upload.py'),
    FieldSpec('proxy_platforms', SECTION_RECORD, '使用代理录制的平台(逗号分隔)', DEFAULT_PROXY_PLATFORMS, 'list',
              '走代理的平台关键字', GROUP_PROXY, help='直播间网址包含这些关键字时使用代理'),
    FieldSpec('extra_proxy_platforms', SECTION_RECORD, '额外使用代理录制的平台(逗号分隔)', '', 'list',
              '额外走代理的平台关键字', GROUP_PROXY, help='即使关闭"使用代理"也会对这些平台使用代理地址'),

    FieldSpec('push_channels', SECTION_PUSH, '直播状态推送渠道', '', 'multi', '推送渠道', GROUP_PUSH, PUSH_CHANNELS),
    FieldSpec('dingtalk_api', SECTION_PUSH, '钉钉推送接口链接', '', 'str', '钉钉 Webhook', GROUP_PUSH),
    FieldSpec('xizhi_api', SECTION_PUSH, '微信推送接口链接', '', 'str', '微信(息知)接口', GROUP_PUSH),
    FieldSpec('bark_api', SECTION_PUSH, 'bark推送接口链接', '', 'str', 'Bark 接口', GROUP_PUSH),
    FieldSpec('bark_level', SECTION_PUSH, 'bark推送中断级别', 'active', 'choice', 'Bark 中断级别', GROUP_PUSH,
              ('active', 'timeSensitive', 'passive', 'critical')),
    FieldSpec('bark_ring', SECTION_PUSH, 'bark推送铃声', 'bell', 'str', 'Bark 铃声', GROUP_PUSH),
    FieldSpec('dingtalk_phone', SECTION_PUSH, '钉钉通知@对象(填手机号)', '', 'str', '钉钉 @手机号', GROUP_PUSH),
    FieldSpec('dingtalk_atall', SECTION_PUSH, '钉钉通知@全体(是/否)', False, 'bool', '钉钉 @全体', GROUP_PUSH),
    FieldSpec('tg_token', SECTION_PUSH, 'tgapi令牌', '', 'password', 'Telegram Bot Token', GROUP_PUSH),
    FieldSpec('tg_chat_id', SECTION_PUSH, 'tg聊天id(个人或者群组id)', '', 'str', 'Telegram Chat ID', GROUP_PUSH),
    FieldSpec('email_host', SECTION_PUSH, 'SMTP邮件服务器', '', 'str', 'SMTP 服务器', GROUP_PUSH),
    FieldSpec('smtp_ssl', SECTION_PUSH, '是否使用SMTP服务SSL加密(是/否)', True, 'bool', 'SMTP 使用 SSL', GROUP_PUSH),
    FieldSpec('smtp_port', SECTION_PUSH, 'SMTP邮件服务器端口', '', 'str', 'SMTP 端口', GROUP_PUSH),
    FieldSpec('login_email', SECTION_PUSH, '邮箱登录账号', '', 'str', '邮箱登录账号', GROUP_PUSH),
    FieldSpec('email_password', SECTION_PUSH, '发件人密码(授权码)', '', 'password', '邮箱密码/授权码', GROUP_PUSH),
    FieldSpec('sender_email', SECTION_PUSH, '发件人邮箱', '', 'str', '发件人邮箱', GROUP_PUSH),
    FieldSpec('sender_name', SECTION_PUSH, '发件人显示昵称', '', 'str', '发件人昵称', GROUP_PUSH),
    FieldSpec('to_email', SECTION_PUSH, '收件人邮箱', '', 'str', '收件人邮箱(逗号分隔)', GROUP_PUSH),
    FieldSpec('ntfy_api', SECTION_PUSH, 'ntfy推送地址', '', 'str', 'ntfy 地址', GROUP_PUSH,
              help='例如 https://ntfy.sh/your_topic'),
    FieldSpec('ntfy_tags', SECTION_PUSH, 'ntfy推送标签', 'tada', 'str', 'ntfy 标签', GROUP_PUSH),
    FieldSpec('ntfy_email', SECTION_PUSH, 'ntfy推送邮箱', '', 'str', 'ntfy 邮箱', GROUP_PUSH),
    FieldSpec('pushplus_token', SECTION_PUSH, 'pushplus推送token', '', 'password', 'PushPlus Token', GROUP_PUSH),
    FieldSpec('push_title', SECTION_PUSH, '自定义推送标题', '直播间状态更新通知', 'str', '推送标题', GROUP_PUSH),
    FieldSpec('begin_push_text', SECTION_PUSH, '自定义开播推送内容', '', 'str', '开播推送内容', GROUP_PUSH,
              help='可用占位符 [直播间名称] [时间]'),
    FieldSpec('over_push_text', SECTION_PUSH, '自定义关播推送内容', '', 'str', '关播推送内容', GROUP_PUSH,
              help='可用占位符 [直播间名称] [时间]'),
    FieldSpec('disable_record', SECTION_PUSH, '只推送通知不录制(是/否)', False, 'bool', '只推送不录制', GROUP_PUSH),
    FieldSpec('push_check_seconds', SECTION_PUSH, '直播推送检测频率(秒)', 1800, 'int', '只推送模式检测间隔(秒)',
              GROUP_PUSH),
    FieldSpec('begin_push', SECTION_PUSH, '开播推送开启(是/否)', True, 'bool', '开播推送', GROUP_PUSH),
    FieldSpec('over_push', SECTION_PUSH, '关播推送开启(是/否)', False, 'bool', '关播推送', GROUP_PUSH),

    FieldSpec('popkontv_token', SECTION_AUTH, 'popkontv_token', '', 'password', 'PopkonTV Token', GROUP_ACCOUNT),
    FieldSpec('sooplive_username', SECTION_ACCOUNT, 'sooplive账号', '', 'str', 'SOOP 账号', GROUP_ACCOUNT),
    FieldSpec('sooplive_password', SECTION_ACCOUNT, 'sooplive密码', '', 'password', 'SOOP 密码', GROUP_ACCOUNT),
    FieldSpec('flextv_username', SECTION_ACCOUNT, 'flextv账号', '', 'str', 'FlexTV 账号', GROUP_ACCOUNT),
    FieldSpec('flextv_password', SECTION_ACCOUNT, 'flextv密码', '', 'password', 'FlexTV 密码', GROUP_ACCOUNT),
    FieldSpec('popkontv_username', SECTION_ACCOUNT, 'popkontv账号', '', 'str', 'PopkonTV 账号', GROUP_ACCOUNT),
    FieldSpec('popkontv_partner_code', SECTION_ACCOUNT, 'partner_code', 'P-00001', 'str', 'PopkonTV partner_code',
              GROUP_ACCOUNT),
    FieldSpec('popkontv_password', SECTION_ACCOUNT, 'popkontv密码', '', 'password', 'PopkonTV 密码', GROUP_ACCOUNT),
    FieldSpec('twitcasting_account_type', SECTION_ACCOUNT, 'twitcasting账号类型', 'normal', 'choice',
              'TwitCasting 账号类型', GROUP_ACCOUNT, ('normal', 'twitter')),
    FieldSpec('twitcasting_username', SECTION_ACCOUNT, 'twitcasting账号', '', 'str', 'TwitCasting 账号',
              GROUP_ACCOUNT),
    FieldSpec('twitcasting_password', SECTION_ACCOUNT, 'twitcasting密码', '', 'password', 'TwitCasting 密码',
              GROUP_ACCOUNT),

    FieldSpec('web_host', SECTION_WEB, '监听地址', '127.0.0.1', 'str', 'Web 监听地址', GROUP_WEB,
              help='0.0.0.0 表示允许局域网/外网访问，此时必须设置访问密码'),
    FieldSpec('web_port', SECTION_WEB, '端口', 8686, 'int', 'Web 端口', GROUP_WEB),
    FieldSpec('web_password', SECTION_WEB, '访问密码', '', 'password', 'Web 访问密码', GROUP_WEB),
)

SPEC_BY_NAME = {spec.name: spec for spec in SPECS}


@dataclass(frozen=True)
class CookieSpec:
    key: str
    ini_key: str
    label: str


COOKIE_SPECS: tuple[CookieSpec, ...] = tuple(CookieSpec(*item) for item in (
    ('douyin', '抖音cookie', '抖音'),
    ('kuaishou', '快手cookie', '快手'),
    ('tiktok', 'tiktok_cookie', 'TikTok'),
    ('huya', '虎牙cookie', '虎牙'),
    ('douyu', '斗鱼cookie', '斗鱼'),
    ('yy', 'yy_cookie', 'YY'),
    ('bilibili', 'B站cookie', 'B站'),
    ('xhs', '小红书cookie', '小红书'),
    ('bigo', 'bigo_cookie', 'Bigo'),
    ('blued', 'blued_cookie', 'Blued'),
    ('sooplive', 'sooplive_cookie', 'SOOP'),
    ('netease', 'netease_cookie', '网易CC'),
    ('qiandurebo', '千度热播_cookie', '千度热播'),
    ('pandatv', 'pandatv_cookie', 'PandaTV'),
    ('maoerfm', '猫耳fm_cookie', '猫耳FM'),
    ('winktv', 'winktv_cookie', 'WinkTV'),
    ('flextv', 'flextv_cookie', 'FlexTV'),
    ('look', 'look_cookie', 'Look'),
    ('twitcasting', 'twitcasting_cookie', 'TwitCasting'),
    ('baidu', 'baidu_cookie', '百度'),
    ('weibo', 'weibo_cookie', '微博'),
    ('kugou', 'kugou_cookie', '酷狗'),
    ('twitch', 'twitch_cookie', 'Twitch'),
    ('liveme', 'liveme_cookie', 'LiveMe'),
    ('huajiao', 'huajiao_cookie', '花椒'),
    ('liuxing', 'liuxing_cookie', '流星'),
    ('showroom', 'showroom_cookie', 'ShowRoom'),
    ('acfun', 'acfun_cookie', 'Acfun'),
    ('changliao', 'changliao_cookie', '畅聊'),
    ('yinbo', 'yinbo_cookie', '音播'),
    ('yingke', 'yingke_cookie', '映客'),
    ('zhihu', 'zhihu_cookie', '知乎'),
    ('chzzk', 'chzzk_cookie', 'CHZZK'),
    ('haixiu', 'haixiu_cookie', '嗨秀'),
    ('vvxqiu', 'vvxqiu_cookie', 'VV星球'),
    ('17live', '17live_cookie', '17Live'),
    ('langlive', 'langlive_cookie', '浪Live'),
    ('pplive', 'pplive_cookie', '漂漂'),
    ('6room', '6room_cookie', '六间房'),
    ('lehaitv', 'lehaitv_cookie', '乐嗨'),
    ('huamao', 'huamao_cookie', '花猫'),
    ('shopee', 'shopee_cookie', 'Shopee'),
    ('youtube', 'youtube_cookie', 'YouTube'),
    ('taobao', 'taobao_cookie', '淘宝'),
    ('jd', 'jd_cookie', '京东'),
    ('faceit', 'faceit_cookie', 'Faceit'),
    ('migu', 'migu_cookie', '咪咕'),
    ('lianjie', 'lianjie_cookie', '连接'),
    ('laixiu', 'laixiu_cookie', '来秀'),
    ('picarto', 'picarto_cookie', 'Picarto'),
))

COOKIE_BY_KEY = {spec.key: spec for spec in COOKIE_SPECS}
SECTION_ORDER = (SECTION_RECORD, SECTION_PUSH, SECTION_COOKIE, SECTION_AUTH, SECTION_ACCOUNT, SECTION_WEB)


def parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    word = raw.strip().lower()
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    return default


def split_list(raw: str) -> list[str]:
    return [item.strip() for item in (raw or '').replace('，', ',').split(',') if item.strip()]


def parse_value(spec: FieldSpec, raw: str | None) -> Any:
    if raw is None:
        return copy.copy(spec.default)
    raw = raw.strip()
    if spec.kind == 'bool':
        return parse_bool(raw, spec.default)
    if spec.kind == 'int':
        try:
            return int(float(raw))
        except ValueError:
            return spec.default
    if spec.kind == 'float':
        try:
            return float(raw)
        except ValueError:
            return spec.default
    return raw


def format_value(spec: FieldSpec, value: Any) -> str:
    if spec.kind == 'bool':
        return '是' if value else '否'
    if spec.kind in ('list', 'multi') and isinstance(value, (list, tuple)):
        return ', '.join(str(v) for v in value)
    return '' if value is None else str(value)


def coerce_value(spec: FieldSpec, value: Any) -> Any:
    """Normalise a value coming from a GUI/HTTP client into the spec's type."""
    if isinstance(value, str):
        if spec.kind == 'multi':
            return value
        return parse_value(spec, value)
    if spec.kind == 'bool':
        return bool(value)
    if spec.kind == 'int':
        return int(value)
    if spec.kind == 'float':
        return float(value)
    if spec.kind in ('list', 'multi') and isinstance(value, (list, tuple)):
        return ', '.join(str(v) for v in value)
    return '' if value is None else str(value)


class Settings:
    def __init__(self, values: dict[str, Any], cookies: dict[str, str]):
        self._values = dict(values)
        self.cookies = dict(cookies)

    def __getattr__(self, name: str) -> Any:
        try:
            return self.__dict__['_values'][name]
        except KeyError:
            raise AttributeError(name) from None

    def get(self, name: str, default: Any = None) -> Any:
        return self._values.get(name, default)

    def cookie(self, key: str) -> str:
        return self.cookies.get(key, '') or ''

    @property
    def values(self) -> dict[str, Any]:
        return dict(self._values)

    @property
    def proxy_platform_list(self) -> list[str]:
        return split_list(self.proxy_platforms)

    @property
    def extra_proxy_platform_list(self) -> list[str]:
        return split_list(self.extra_proxy_platforms)

    @property
    def push_channel_list(self) -> list[str]:
        return [ch for ch in PUSH_CHANNELS if ch in (self.push_channels or '').upper()]

    @property
    def save_format_code(self) -> str:
        fmt = (self.save_format or 'ts').upper()
        return fmt if fmt in ("FLV", "MKV", "TS", "MP4", "MP3音频", "M4A音频", "MP3", "M4A") else "TS"

    def effective_proxy(self) -> str | None:
        return self.proxy_addr if self.use_proxy and self.proxy_addr else None

    def to_dict(self) -> dict[str, Any]:
        return {'values': self.values, 'cookies': dict(self.cookies)}


_SECTION_RE = re.compile(r'^\s*\[(?P<name>[^\]]+)\]\s*$')
_OPTION_RE = re.compile(r'^(?P<key>[^#;\s\[][^=:]*?)\s*(?P<sep>[=:])\s*(?P<value>.*)$')


class IniDocument:
    """Minimal ini editor that keeps comments, blank lines and key order intact.

    Option names are matched case-insensitively and stripped, the same way configparser does,
    so files previously written by configparser (which lower-cases keys) stay compatible.
    """

    def __init__(self, text: str = ''):
        self.lines: list[str] = text.splitlines()

    @classmethod
    def load(cls, path: Path) -> "IniDocument":
        if not path.exists():
            return cls('')
        return cls(path.read_text(encoding=TEXT_ENCODING, errors='ignore'))

    def text(self) -> str:
        return '\n'.join(self.lines).rstrip('\n') + '\n'

    def _sections(self) -> list[tuple[str, int, int]]:
        sections = []
        current_name, current_start = None, -1
        for index, line in enumerate(self.lines):
            match = _SECTION_RE.match(line)
            if match:
                if current_name is not None:
                    sections.append((current_name, current_start, index))
                current_name, current_start = match.group('name').strip(), index
        if current_name is not None:
            sections.append((current_name, current_start, len(self.lines)))
        return sections

    def _find_section(self, section: str) -> tuple[int, int] | None:
        for name, start, end in self._sections():
            if name == section:
                return start, end
        return None

    def _find_option(self, section: str, key: str) -> int | None:
        bounds = self._find_section(section)
        if not bounds:
            return None
        wanted = key.strip().lower()
        for index in range(bounds[0] + 1, bounds[1]):
            match = _OPTION_RE.match(self.lines[index])
            if match and match.group('key').strip().lower() == wanted:
                return index
        return None

    def has_section(self, section: str) -> bool:
        return self._find_section(section) is not None

    def get(self, section: str, key: str) -> str | None:
        index = self._find_option(section, key)
        if index is None:
            return None
        return _OPTION_RE.match(self.lines[index]).group('value').strip()

    def set(self, section: str, key: str, value: str) -> bool:
        value = '' if value is None else str(value).replace('\r', '').replace('\n', ' ')
        index = self._find_option(section, key)
        if index is not None:
            match = _OPTION_RE.match(self.lines[index])
            if match.group('value').strip() == value:
                return False
            self.lines[index] = self._option_line(match.group('key').rstrip(), value)
            return True

        bounds = self._find_section(section)
        if bounds is None:
            if self.lines and self.lines[-1].strip():
                self.lines.append('')
            self.lines.append(f'[{section}]')
            self.lines.append(self._option_line(key, value))
            return True

        insert_at = bounds[1]
        while insert_at - 1 > bounds[0] and not self.lines[insert_at - 1].strip():
            insert_at -= 1
        self.lines.insert(insert_at, self._option_line(key, value))
        return True

    @staticmethod
    def _option_line(key: str, value: str) -> str:
        return f'{key} = {value}' if value else f'{key} ='

    def save(self, path: Path) -> None:
        atomic_write_text(path, self.text())


class ConfigStore:
    def __init__(self, path: Path, example_path: Path | None = None):
        self.path = Path(path)
        self.example_path = Path(example_path) if example_path else None
        self._lock = threading.RLock()
        self._mtime: float | None = None

    def ensure_exists(self) -> None:
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.example_path and self.example_path.exists():
            shutil.copyfile(self.example_path, self.path)
        else:
            self.path.write_text('', encoding=TEXT_ENCODING)

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

    def load(self) -> Settings:
        with self._lock:
            self.ensure_exists()
            doc = IniDocument.load(self.path)
            modified = False
            for section in SECTION_ORDER:
                if not doc.has_section(section):
                    if doc.lines and doc.lines[-1].strip():
                        doc.lines.append('')
                    doc.lines.append(f'[{section}]')
                    modified = True

            values: dict[str, Any] = {}
            for spec in SPECS:
                raw = doc.get(spec.section, spec.key)
                if raw is None:
                    doc.set(spec.section, spec.key, format_value(spec, spec.default))
                    modified = True
                values[spec.name] = parse_value(spec, raw)

            cookies: dict[str, str] = {}
            for spec in COOKIE_SPECS:
                raw = doc.get(SECTION_COOKIE, spec.ini_key)
                if raw is None:
                    doc.set(SECTION_COOKIE, spec.ini_key, '')
                    modified = True
                cookies[spec.key] = (raw or '').strip()

            if modified:
                doc.save(self.path)
            self._remember_mtime()
            return Settings(values, cookies)

    def save(self, values: dict[str, Any] | None = None, cookies: dict[str, str] | None = None) -> Settings:
        with self._lock:
            self.ensure_exists()
            doc = IniDocument.load(self.path)
            changed = False
            for name, value in (values or {}).items():
                spec = SPEC_BY_NAME.get(name)
                if not spec:
                    raise KeyError(f'未知配置项: {name}')
                changed |= doc.set(spec.section, spec.key, format_value(spec, coerce_value(spec, value)))
            for key, value in (cookies or {}).items():
                spec = COOKIE_BY_KEY.get(key)
                if not spec:
                    raise KeyError(f'未知 Cookie 项: {key}')
                changed |= doc.set(SECTION_COOKIE, spec.ini_key, (value or '').strip())
            if changed:
                doc.save(self.path)
        return self.load()

    def set_raw(self, section: str, key: str, value: str) -> None:
        with self._lock:
            doc = IniDocument.load(self.path)
            if doc.set(section, key, value):
                doc.save(self.path)
            self._remember_mtime()


def file_md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


class BackupManager:
    """Copies files into backup_dir whenever their content changes, keeping the newest `limit` copies."""

    def __init__(self, files: Iterable[Path], backup_dir: Path, limit: int = 6):
        self.files = [Path(f) for f in files]
        self.backup_dir = Path(backup_dir)
        self.limit = limit
        self._last_md5: dict[Path, str] = {}

    def run_once(self) -> list[Path]:
        created = []
        for path in self.files:
            if not path.exists():
                continue
            digest = file_md5(path)
            if self._last_md5.get(path) == digest:
                continue
            self._last_md5[path] = digest
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            target = self.backup_dir / f'{path.name}_{timestamp}'
            shutil.copy2(path, target)
            created.append(target)
            self._prune(path.name)
        return created

    def _prune(self, prefix: str) -> None:
        backups = sorted(
            (p for p in self.backup_dir.iterdir() if p.name.startswith(prefix + '_')),
            key=lambda p: p.stat().st_mtime,
        )
        for old in backups[:-self.limit] if len(backups) > self.limit else []:
            old.unlink(missing_ok=True)
