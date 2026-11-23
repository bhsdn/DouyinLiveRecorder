# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
from typing import Callable

from msg_push import bark, dingtalk, ntfy, pushplus, send_email, tg_bot, xizhi

from .settings import PUSH_CHANNELS, Settings

DEFAULT_BEGIN_TEXT = "直播间状态更新：[直播间名称] 正在直播中，时间：[时间]"
DEFAULT_OVER_TEXT = "直播间状态更新：[直播间名称] 直播已结束！时间：[时间]"


def render(template: str, default: str, room_name: str, when: datetime.datetime | None = None) -> str:
    when = when or datetime.datetime.now()
    text = template or default
    text = text.replace('[直播间名称]', room_name).replace('[时间]', when.strftime('%Y-%m-%d %H:%M:%S'))
    return text.replace(r'\n', '\n')


class Notifier:
    def __init__(self, settings_provider: Callable[[], Settings]):
        self._settings = settings_provider

    def _senders(self, s: Settings, live_url: str, content: str) -> dict[str, Callable[[], dict]]:
        title = (s.push_title or '').strip() or "直播间状态更新通知"
        return {
            '微信': lambda: xizhi(s.xizhi_api, title, content),
            '钉钉': lambda: dingtalk(s.dingtalk_api, content, s.dingtalk_phone, s.dingtalk_atall),
            '邮箱': lambda: send_email(s.email_host, s.login_email, s.email_password, s.sender_email,
                                     s.sender_name, s.to_email, title, content, s.smtp_port, s.smtp_ssl),
            'TG': lambda: tg_bot(s.tg_chat_id, s.tg_token, content),
            'BARK': lambda: bark(s.bark_api, title=title, content=content, level=s.bark_level, sound=s.bark_ring),
            'NTFY': lambda: ntfy(s.ntfy_api, title=title, content=content, tags=s.ntfy_tags, action_url=live_url,
                                 email=s.ntfy_email),
            'PUSHPLUS': lambda: pushplus(s.pushplus_token, title, content),
        }

    def send(self, live_url: str, content: str, channels: list[str] | None = None) -> dict[str, str]:
        """Send to the configured channels; returns a human readable result per channel."""
        s = self._settings()
        channels = channels if channels is not None else s.push_channel_list
        senders = self._senders(s, live_url, content)
        results: dict[str, str] = {}
        for channel in PUSH_CHANNELS:
            if channel not in channels:
                continue
            try:
                result = senders[channel]()
                results[channel] = f'成功{len(result["success"])}, 失败{len(result["error"])}'
            except Exception as e:
                results[channel] = f'失败: {e}'
        return results

    def begin_text(self, room_name: str) -> str:
        return render(self._settings().begin_push_text, DEFAULT_BEGIN_TEXT, room_name)

    def over_text(self, room_name: str) -> str:
        return render(self._settings().over_push_text, DEFAULT_OVER_TEXT, room_name)
