# -*- coding: utf-8 -*-
"""Manual check: resolve sample live rooms through the platform registry.

    python tests/manual_fetch_check.py [url ...]

Needs network access; not part of the pytest suite.
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DLR_SKIP_AUTO_INSTALL', '1')

from recorder_core.common import quality_code  # noqa: E402
from recorder_core.paths import AppPaths  # noqa: E402
from recorder_core.platforms import FetchContext, fetch_stream, find_platform, resolve_proxy  # noqa: E402
from recorder_core.settings import ConfigStore  # noqa: E402

SAMPLES = [
    "https://live.douyin.com/745964462470",
    "https://live.bilibili.com/21593109",
    "https://www.huya.com/116",
    "https://www.douyu.com/topic/wzDBLS6?rid=4921614&dyshid=",
    "https://live.kuaishou.com/u/yall1102",
    "https://cc.163.com/583946984",
]


def main(urls: list[str]) -> None:
    paths = AppPaths(ROOT)
    settings = ConfigStore(paths.config_file, paths.example_config_file).load()
    for url in urls:
        platform = find_platform(url)
        if not platform:
            print(f'[unsupported] {url}')
            continue
        ctx = FetchContext(url=url, quality=quality_code(settings.quality), proxy=resolve_proxy(url, settings),
                           settings=settings, global_proxy=False, platform=platform)
        try:
            info = asyncio.run(fetch_stream(platform, ctx)).info
            stream_url = info.get('record_url') or ''
            print(f"[{platform.name}] anchor={info.get('anchor_name')!r} live={info.get('is_live')} "
                  f"title={info.get('title')!r} url={stream_url[:80]}")
        except Exception as e:
            print(f'[{platform.name}] ERROR {type(e).__name__}: {e}')


if __name__ == '__main__':
    main(sys.argv[1:] or SAMPLES)
