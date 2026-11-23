# -*- coding: utf-8 -*-
"""Platform registry.

Each entry maps URL fragments to an async adapter that returns the normalised stream info
dict produced by src/stream.py (keys: anchor_name, is_live, title, quality, m3u8_url,
flv_url, record_url). Entries are matched in registration order, which mirrors the
original if/elif chain, so ambiguous fragments such as "3.cn" resolve the same way.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from src import spider, stream

from .settings import SECTION_AUTH, SECTION_COOKIE, Settings


class PlatformError(Exception):
    pass


@dataclass
class FetchContext:
    url: str
    quality: str
    proxy: str | None
    settings: Settings
    global_proxy: bool
    platform: "Platform"

    @property
    def cookies(self) -> str:
        return self.settings.cookie(self.platform.cookie_key) if self.platform.cookie_key else ''


@dataclass
class FetchResult:
    info: dict
    new_url: str | None = None
    config_updates: list[tuple[str, str, str]] = field(default_factory=list)


Adapter = Callable[[FetchContext], Awaitable[Any]]


@dataclass(frozen=True)
class Platform:
    name: str
    match: tuple[str, ...]
    fetch: Adapter
    cookie_key: str = ''
    requires_proxy: bool = False
    only_flv: bool = False
    only_audio: bool = False
    force_http: bool = False
    flv_preferred: bool = False
    record_header: str | None = None
    custom: bool = False

    def matches(self, url: str) -> bool:
        return any(fragment in url for fragment in self.match)

    def header_for(self, url: str) -> str | None:
        if not self.record_header:
            return None
        live_domain = '/'.join(url.split('/')[0:3])
        return self.record_header.replace('{live_domain}', live_domain)


SUPPORTED_HOSTS = (
    'live.douyin.com', 'v.douyin.com', 'www.douyin.com', 'live.kuaishou.com', 'www.huya.com', 'www.douyu.com',
    'www.yy.com', 'live.bilibili.com', 'www.redelight.cn', 'www.xiaohongshu.com', 'xhslink.com', 'www.bigo.tv',
    'slink.bigovideo.tv', 'app.blued.cn', 'cc.163.com', 'qiandurebo.com', 'fm.missevan.com', 'look.163.com',
    'twitcasting.tv', 'live.baidu.com', 'weibo.com', 'fanxing.kugou.com', 'fanxing2.kugou.com',
    'mfanxing.kugou.com', 'www.huajiao.com', 'www.7u66.com', 'wap.7u66.com', 'live.acfun.cn', 'm.acfun.cn',
    'live.tlclw.com', 'wap.tlclw.com', 'live.ybw1666.com', 'wap.ybw1666.com', 'www.inke.cn', 'www.zhihu.com',
    'www.haixiutv.com', "h5webcdnp.vvxqiu.com", "17.live", 'www.lang.live', "m.pp.weimipopo.com", "v.6.cn",
    "m.6.cn", 'www.lehaitv.com', 'h.catshow168.com', 'e.tb.cn', 'huodong.m.taobao.com', '3.cn', 'eco.m.jd.com',
    'www.miguvideo.com', 'm.miguvideo.com', 'show.lailianjie.com', 'www.imkktv.com', 'www.picarto.tv',
)

OVERSEAS_HOSTS = (
    'www.tiktok.com', 'play.sooplive.co.kr', 'm.sooplive.co.kr', 'www.sooplive.com', 'm.sooplive.com',
    'www.pandalive.co.kr', 'www.winktv.co.kr', 'www.flextv.co.kr', 'www.ttinglive.com', 'www.popkontv.com',
    'www.twitch.tv', 'www.liveme.com', 'www.showroom-live.com', 'chzzk.naver.com', 'm.chzzk.naver.com',
    'live.shopee.', '.shp.ee', 'www.youtube.com', 'youtu.be', 'www.faceit.com',
)


def is_supported_url(url: str) -> bool:
    parts = url.split('/')
    host = parts[2] if len(parts) > 2 else ''
    if 'live.shopee.' in host or '.shp.ee' in host:
        return True
    if host in SUPPORTED_HOSTS or host in OVERSEAS_HOSTS:
        return True
    return any(ext in url for ext in (".flv", ".m3u8"))


def is_overseas_url(url: str) -> bool:
    return any(host in url for host in OVERSEAS_HOSTS)


_REGISTRY: list[Platform] = []


def register(name: str, match: tuple[str, ...] | str, cookie_key: str = '', **options):
    if isinstance(match, str):
        match = (match,)

    def decorator(func: Adapter) -> Adapter:
        _REGISTRY.append(Platform(name=name, match=tuple(match), fetch=func, cookie_key=cookie_key, **options))
        return func

    return decorator


def all_platforms() -> list[Platform]:
    return list(_REGISTRY)


def find_platform(url: str) -> Platform | None:
    for platform in _REGISTRY:
        if platform.matches(url):
            return platform
    return None


def platform_names() -> list[str]:
    return [p.name for p in _REGISTRY]


async def _generic(ctx: FetchContext, fetcher, **stream_kwargs) -> dict:
    data = await fetcher(url=ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies)
    return await stream.get_stream_url(data, ctx.quality, **stream_kwargs)


async def _direct(ctx: FetchContext, fetcher) -> dict:
    return await fetcher(url=ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies)


# ---------------------------------------------------------------- domestic / mixed, original order

@register('抖音直播', 'douyin.com/', 'douyin', flv_preferred=True)
async def _douyin(ctx: FetchContext):
    if 'v.douyin.com' not in ctx.url and '/user/' not in ctx.url:
        data = await spider.get_douyin_web_stream_data(url=ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies)
    else:
        data = await spider.get_douyin_app_stream_data(url=ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies)
    return await stream.get_douyin_stream_url(data, ctx.quality, ctx.proxy)


@register('TikTok直播', 'https://www.tiktok.com/', 'tiktok', requires_proxy=True, flv_preferred=True)
async def _tiktok(ctx: FetchContext):
    data = await spider.get_tiktok_stream_data(url=ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies)
    return await stream.get_tiktok_stream_url(data, ctx.quality, ctx.proxy)


@register('快手直播', 'https://live.kuaishou.com/', 'kuaishou')
async def _kuaishou(ctx: FetchContext):
    data = await spider.get_kuaishou_stream_data(url=ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies)
    return await stream.get_kuaishou_stream_url(data, ctx.quality)


@register('虎牙直播', 'https://www.huya.com/', 'huya')
async def _huya(ctx: FetchContext):
    if ctx.quality not in ('OD', 'BD', 'UHD'):
        data = await spider.get_huya_stream_data(url=ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies)
        return await stream.get_huya_stream_url(data, ctx.quality)
    return await spider.get_huya_app_stream_url(url=ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies)


@register('斗鱼直播', 'https://www.douyu.com/', 'douyu')
async def _douyu(ctx: FetchContext):
    data = await spider.get_douyu_info_data(url=ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies)
    return await stream.get_douyu_stream_url(data, video_quality=ctx.quality, cookies=ctx.cookies,
                                             proxy_addr=ctx.proxy)


@register('YY直播', 'https://www.yy.com/', 'yy')
async def _yy(ctx: FetchContext):
    data = await spider.get_yy_stream_data(url=ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies)
    return await stream.get_yy_stream_url(data)


@register('B站直播', 'https://live.bilibili.com/', 'bilibili')
async def _bilibili(ctx: FetchContext):
    data = await spider.get_bilibili_room_info(url=ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies)
    return await stream.get_bilibili_stream_url(data, video_quality=ctx.quality, cookies=ctx.cookies,
                                                proxy_addr=ctx.proxy)


@register('小红书直播', ('http://xhslink.com/', 'https://www.xiaohongshu.com/'), 'xhs')
async def _xhs(ctx: FetchContext):
    return await spider.get_xhs_stream_url(ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies)


@register('Bigo直播', ('www.bigo.tv/', 'slink.bigovideo.tv/'), 'bigo')
async def _bigo(ctx: FetchContext):
    return await spider.get_bigo_stream_url(ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies)


@register('Blued直播', 'https://app.blued.cn/', 'blued', record_header='referer:https://app.blued.cn')
async def _blued(ctx: FetchContext):
    return await spider.get_blued_stream_url(ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies)


@register('SOOP', ('sooplive.co.kr/', 'sooplive.com/'), 'sooplive', requires_proxy=True)
async def _soop(ctx: FetchContext):
    data = await spider.get_sooplive_stream_data(
        url=ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies,
        username=ctx.settings.sooplive_username, password=ctx.settings.sooplive_password)
    updates = []
    if data and data.get('new_cookies'):
        updates.append((SECTION_COOKIE, 'sooplive_cookie', data['new_cookies']))
    info = await stream.get_stream_url(data, ctx.quality, spec=True)
    return FetchResult(info, config_updates=updates)


@register('网易CC直播', 'cc.163.com/', 'netease')
async def _netease(ctx: FetchContext):
    data = await spider.get_netease_stream_data(url=ctx.url, cookies=ctx.cookies)
    return await stream.get_netease_stream_url(data, ctx.quality)


@register('千度热播', 'qiandurebo.com/', 'qiandurebo', record_header='referer:https://qiandurebo.com')
async def _qiandurebo(ctx: FetchContext):
    return await _direct(ctx, spider.get_qiandurebo_stream_data)


@register('PandaTV', 'www.pandalive.co.kr/', 'pandatv', requires_proxy=True,
          record_header='origin:https://www.pandalive.co.kr')
async def _pandatv(ctx: FetchContext):
    return await _generic(ctx, spider.get_pandatv_stream_data, spec=True)


@register('猫耳FM直播', 'fm.missevan.com/', 'maoerfm', only_audio=True)
async def _maoerfm(ctx: FetchContext):
    return await _direct(ctx, spider.get_maoerfm_stream_url)


@register('WinkTV', 'www.winktv.co.kr/', 'winktv', requires_proxy=True,
          record_header='origin:https://www.winktv.co.kr')
async def _winktv(ctx: FetchContext):
    return await _generic(ctx, spider.get_winktv_stream_data, spec=True)


@register('FlexTV', ('www.flextv.co.kr/', 'www.ttinglive.com/'), 'flextv', requires_proxy=True,
          record_header='origin:https://www.flextv.co.kr')
async def _flextv(ctx: FetchContext):
    data = await spider.get_flextv_stream_data(
        url=ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies,
        username=ctx.settings.flextv_username, password=ctx.settings.flextv_password)
    updates = []
    if data and data.get('new_cookies'):
        updates.append((SECTION_COOKIE, 'flextv_cookie', data['new_cookies']))
    if data and 'play_url_list' in data:
        info = await stream.get_stream_url(data, ctx.quality, spec=True)
    else:
        info = data
    return FetchResult(info, config_updates=updates)


@register('Look直播', 'look.163.com/', 'look', only_audio=True)
async def _look(ctx: FetchContext):
    return await _direct(ctx, spider.get_looklive_stream_url)


@register('PopkonTV', 'www.popkontv.com/', requires_proxy=True, record_header='origin:https://www.popkontv.com')
async def _popkontv(ctx: FetchContext):
    s = ctx.settings
    info = await spider.get_popkontv_stream_url(
        url=ctx.url, proxy_addr=ctx.proxy, access_token=s.popkontv_token, username=s.popkontv_username,
        password=s.popkontv_password, partner_code=s.popkontv_partner_code)
    updates = []
    if info and info.get('new_token'):
        updates.append((SECTION_AUTH, 'popkontv_token', info['new_token']))
    return FetchResult(info, config_updates=updates)


@register('TwitCasting', 'twitcasting.tv/', 'twitcasting')
async def _twitcasting(ctx: FetchContext):
    s = ctx.settings
    data = await spider.get_twitcasting_stream_url(
        url=ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies, account_type=s.twitcasting_account_type,
        username=s.twitcasting_username, password=s.twitcasting_password)
    updates = []
    if data and data.get('new_cookies'):
        updates.append((SECTION_COOKIE, 'twitcasting_cookie', data['new_cookies']))
    info = await stream.get_stream_url(data, ctx.quality, spec=False)
    return FetchResult(info, config_updates=updates)


@register('百度直播', 'live.baidu.com/', 'baidu')
async def _baidu(ctx: FetchContext):
    return await _generic(ctx, spider.get_baidu_stream_data)


@register('微博直播', 'weibo.com/', 'weibo')
async def _weibo(ctx: FetchContext):
    return await _generic(ctx, spider.get_weibo_stream_data, hls_extra_key='m3u8_url')


@register('酷狗直播', 'kugou.com/', 'kugou')
async def _kugou(ctx: FetchContext):
    return await _direct(ctx, spider.get_kugou_stream_url)


@register('TwitchTV', 'www.twitch.tv/', 'twitch', requires_proxy=True)
async def _twitch(ctx: FetchContext):
    return await _generic(ctx, spider.get_twitchtv_stream_data, spec=True)


@register('LiveMe', 'www.liveme.com/', 'liveme', requires_proxy=True)
async def _liveme(ctx: FetchContext):
    return await _direct(ctx, spider.get_liveme_stream_url)


@register('花椒直播', 'www.huajiao.com/', 'huajiao', only_flv=True)
async def _huajiao(ctx: FetchContext):
    return await _direct(ctx, spider.get_huajiao_stream_url)


@register('流星直播', '7u66.com/', 'liuxing')
async def _liuxing(ctx: FetchContext):
    return await _direct(ctx, spider.get_liuxing_stream_url)


@register('ShowRoom', 'showroom-live.com/', 'showroom')
async def _showroom(ctx: FetchContext):
    return await _generic(ctx, spider.get_showroom_stream_data, spec=True)


@register('Acfun', ('live.acfun.cn/', 'm.acfun.cn/'), 'acfun')
async def _acfun(ctx: FetchContext):
    return await _generic(ctx, spider.get_acfun_stream_data, url_type='flv', flv_extra_key='url')


@register('畅聊直播', 'live.tlclw.com/', 'changliao')
async def _changliao(ctx: FetchContext):
    return await _direct(ctx, spider.get_changliao_stream_url)


@register('音播直播', 'ybw1666.com/', 'yinbo')
async def _yinbo(ctx: FetchContext):
    return await _direct(ctx, spider.get_yinbo_stream_url)


@register('映客直播', 'www.inke.cn/', 'yingke')
async def _yingke(ctx: FetchContext):
    return await _direct(ctx, spider.get_yingke_stream_url)


@register('知乎直播', 'www.zhihu.com/', 'zhihu')
async def _zhihu(ctx: FetchContext):
    return await _direct(ctx, spider.get_zhihu_stream_url)


@register('CHZZK', 'chzzk.naver.com/', 'chzzk')
async def _chzzk(ctx: FetchContext):
    return await _generic(ctx, spider.get_chzzk_stream_data, spec=True)


@register('嗨秀直播', 'www.haixiutv.com/', 'haixiu')
async def _haixiu(ctx: FetchContext):
    return await _direct(ctx, spider.get_haixiu_stream_url)


@register('VV星球', 'vvxqiu.com/', 'vvxqiu')
async def _vvxqiu(ctx: FetchContext):
    return await _direct(ctx, spider.get_vvxqiu_stream_url)


@register('17Live', '17.live/', '17live', record_header='referer:https://17.live/en/live/6302408')
async def _17live(ctx: FetchContext):
    return await _direct(ctx, spider.get_17live_stream_url)


@register('浪Live', 'www.lang.live/', 'langlive', record_header='referer:https://www.lang.live')
async def _langlive(ctx: FetchContext):
    return await _direct(ctx, spider.get_langlive_stream_url)


@register('漂漂直播', 'm.pp.weimipopo.com/', 'pplive')
async def _pplive(ctx: FetchContext):
    return await _direct(ctx, spider.get_pplive_stream_url)


@register('六间房直播', '.6.cn/', '6room')
async def _6room(ctx: FetchContext):
    return await _direct(ctx, spider.get_6room_stream_url)


@register('乐嗨直播', 'lehaitv.com/', 'lehaitv')
async def _lehai(ctx: FetchContext):
    return await _direct(ctx, spider.get_haixiu_stream_url)


@register('花猫直播', 'h.catshow168.com/', 'huamao')
async def _huamao(ctx: FetchContext):
    return await _direct(ctx, spider.get_pplive_stream_url)


@register('shopee', ('live.shopee', 'shp.ee/'), 'shopee', only_flv=True, force_http=True,
          record_header='origin:{live_domain}')
async def _shopee(ctx: FetchContext):
    info = await spider.get_shopee_stream_url(url=ctx.url, proxy_addr=ctx.proxy, cookies=ctx.cookies)
    new_url = None
    if info and info.get('uid'):
        new_url = ctx.url.split('?')[0] + '?' + str(info['uid'])
    return FetchResult(info, new_url=new_url)


@register('Youtube', ('www.youtube.com/', 'youtu.be/'), 'youtube')
async def _youtube(ctx: FetchContext):
    return await _generic(ctx, spider.get_youtube_stream_url, spec=True)


@register('淘宝直播', 'tb.cn', 'taobao')
async def _taobao(ctx: FetchContext):
    return await _generic(ctx, spider.get_taobao_stream_url, url_type='all', hls_extra_key='hlsUrl',
                          flv_extra_key='flvUrl')


@register('京东直播', ('3.cn', 'm.jd.com'), 'jd')
async def _jd(ctx: FetchContext):
    return await _direct(ctx, spider.get_jd_stream_url)


@register('faceit', 'faceit.com/', 'faceit', requires_proxy=True)
async def _faceit(ctx: FetchContext):
    return await _generic(ctx, spider.get_faceit_stream_data, spec=True)


@register('咪咕直播', ('www.miguvideo.com', 'm.miguvideo.com'), 'migu', force_http=True)
async def _migu(ctx: FetchContext):
    return await _direct(ctx, spider.get_migu_stream_url)


@register('连接直播', 'show.lailianjie.com', 'lianjie')
async def _lianjie(ctx: FetchContext):
    return await _direct(ctx, spider.get_lianjie_stream_url)


@register('来秀直播', 'www.imkktv.com', 'laixiu')
async def _laixiu(ctx: FetchContext):
    return await _direct(ctx, spider.get_laixiu_stream_url)


@register('Picarto', 'www.picarto.tv', 'picarto')
async def _picarto(ctx: FetchContext):
    return await _direct(ctx, spider.get_picarto_stream_url)


@register('自定义录制直播', ('.m3u8', '.flv'), custom=True)
async def _custom(ctx: FetchContext):
    info = {
        "anchor_name": '自定义录制直播_' + str(uuid.uuid4())[:8],
        "is_live": True,
        "record_url": ctx.url,
    }
    if '.flv' in ctx.url:
        info['flv_url'] = ctx.url
    else:
        info['m3u8_url'] = ctx.url
    return info


def resolve_proxy(url: str, settings: Settings) -> str | None:
    """Same proxy decision as the original start_record()."""
    proxy_addr = settings.effective_proxy()
    proxy = None
    if proxy_addr:
        for keyword in settings.proxy_platform_list:
            if keyword in url:
                proxy = proxy_addr
                break
    if not proxy:
        for keyword in settings.extra_proxy_platform_list:
            if keyword in url:
                proxy = settings.proxy_addr or None
    return proxy


async def fetch_stream(platform: Platform, ctx: FetchContext) -> FetchResult:
    if platform.requires_proxy and not (ctx.global_proxy or ctx.proxy):
        raise PlatformError(f"网络异常，请检查本网络是否能正常访问{platform.name}平台（需要代理）")
    result = await platform.fetch(ctx)
    if not isinstance(result, FetchResult):
        result = FetchResult(result)
    if not isinstance(result.info, dict):
        result.info = {}
    return result
