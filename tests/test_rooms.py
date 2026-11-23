# -*- coding: utf-8 -*-
import pytest

from recorder_core.platforms import find_platform, is_supported_url
from recorder_core.rooms import RoomStore, parse_line


@pytest.mark.parametrize('line, quality, url, name, enabled', [
    ('https://live.douyin.com/745964462470', '原画', 'https://live.douyin.com/745964462470', '', True),
    ('超清，https://live.douyin.com/745964462470', '超清', 'https://live.douyin.com/745964462470', '', True),
    ('https://www.huya.com/116,主播: 张三', '原画', 'https://www.huya.com/116', '张三', True),
    ('#高清,https://live.bilibili.com/21593109?spm=1,主播: 李四', '高清', 'https://live.bilibili.com/21593109',
     '李四', False),
    ('乱写,https://www.douyu.com/topic/abc?rid=1', '原画', 'https://www.douyu.com/topic/abc?rid=1', '', True),
    ('live.douyin.com/745964462470', '原画', 'https://live.douyin.com/745964462470', '', True),
])
def test_parse_line(line, quality, url, name, enabled):
    room = parse_line(line, '原画', is_supported_url).room
    assert (room.quality, room.url, room.name, room.enabled) == (quality, url, name, enabled)


def test_parse_line_rejects_unknown_hosts_and_short_lines():
    assert parse_line('https://example.com/live/123456', '原画', is_supported_url).unsupported
    assert parse_line('# 我的列表', '原画', is_supported_url).room is None


def test_store_dedupes_comments_unknown_and_round_trips(tmp_path):
    path = tmp_path / 'URL_config.ini'
    path.write_text(
        '超清,https://live.douyin.com/1111111111\n'
        'https://live.douyin.com/1111111111\n'
        'https://example.com/live/123456\n'
        '#https://www.huya.com/116,主播: 张三\n',
        encoding='utf-8-sig')
    store = RoomStore(path, is_supported_url)
    rooms = store.load()
    assert [r.url for r in rooms] == ['https://live.douyin.com/1111111111', 'https://www.huya.com/116']
    assert rooms[1].enabled is False and rooms[1].name == '张三'
    text = path.read_text(encoding='utf-8-sig')
    assert '#https://example.com/live/123456' in text
    assert text.count('live.douyin.com/1111111111') == 1

    rooms[0].name = '王五'
    store.save(rooms)
    reloaded = RoomStore(path, is_supported_url).load()
    assert reloaded[0].name == '王五' and reloaded[0].quality == '超清'
    assert '超清,https://live.douyin.com/1111111111,主播: 王五' in path.read_text(encoding='utf-8-sig')


@pytest.mark.parametrize('url, platform', [
    ('https://live.douyin.com/1', '抖音直播'),
    ('https://v.douyin.com/iQLgKSj/', '抖音直播'),
    ('https://www.tiktok.com/@a/live', 'TikTok直播'),
    ('https://v.6.cn/634435', '六间房直播'),
    ('https://3.cn/28MLBy-E', '京东直播'),
    ('https://e.tb.cn/h.xxx', '淘宝直播'),
    ('https://sg.shp.ee/GmpXeuf', 'shopee'),
    ('https://www.ttinglive.com/channels/1/live', 'FlexTV'),
    ('https://example.com/a.m3u8', '自定义录制直播'),
])
def test_platform_matching_order(url, platform):
    assert find_platform(url).name == platform
