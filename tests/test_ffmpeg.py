# -*- coding: utf-8 -*-
import pytest

from recorder_core.ffmpeg import build_save_dir, input_args, output_args, plan_recording


def test_input_args_match_original_defaults():
    command = input_args('https://x/live.flv')
    assert command[:2] == ['ffmpeg', '-y']
    assert command[command.index('-rw_timeout') + 1] == '15000000'
    assert command[command.index('-i') + 1] == 'https://x/live.flv'
    assert command[-2:] == ['-avoid_negative_ts', '1']


def test_input_args_overseas_proxy_and_headers():
    command = input_args('https://x/live.m3u8', proxy='127.0.0.1:7890', header='origin:https://a', overseas=True)
    assert command[1:3] == ['-http_proxy', 'http://127.0.0.1:7890']
    assert command[command.index('-headers') + 1] == 'origin:https://a'
    assert command.index('-headers') < command.index('-protocol_whitelist') < command.index('-i')
    assert command[command.index('-rw_timeout') + 1] == '50000000'
    assert command[command.index('-bufsize') + 1] == '15000k'


@pytest.mark.parametrize('save_type, split, expected', [
    ('TS', False, ['-c:v', 'copy', '-c:a', 'copy', '-map', '0', '-f', 'mpegts', 'out']),
    ('TS', True, ['-c:v', 'copy', '-c:a', 'copy', '-map', '0', '-f', 'segment', '-segment_time', '60',
                  '-segment_format', 'mpegts', '-reset_timestamps', '1', 'out']),
    ('FLV', False, ['-map', '0', '-c:v', 'copy', '-c:a', 'copy', '-bsf:a', 'aac_adtstoasc', '-f', 'flv', 'out']),
    ('MKV', False, ['-flags', 'global_header', '-map', '0', '-c:v', 'copy', '-c:a', 'copy', '-f', 'matroska',
                    'out']),
    ('MP4', True, ['-c:v', 'copy', '-c:a', 'aac', '-map', '0', '-f', 'segment', '-segment_time', '60',
                   '-segment_format', 'mp4', '-reset_timestamps', '1', '-movflags', '+frag_keyframe+empty_moov',
                   'out']),
    ('MP3', False, ['-map', '0:a', '-c:a', 'libmp3lame', '-ab', '320k', 'out']),
    ('M4A', False, ['-map', '0:a', '-c:a', 'aac', '-bsf:a', 'aac_adtstoasc', '-ab', '320k',
                    '-movflags', '+faststart', 'out']),
])
def test_output_args(save_type, split, expected):
    assert output_args(save_type, 'out', split, 60) == expected


def test_plan_paths_and_types():
    plan = plan_recording('u', 'd/a_2024', 'mp3音频', split=True, split_time=10)
    assert plan.save_type == 'MP3' and plan.output_path == 'd/a_2024_%03d.mp3' and plan.segmented

    audio_only = plan_recording('u', 'd/a', 'TS', audio_only=True)
    assert audio_only.save_type == 'MP3' and audio_only.output_path == 'd/a.mp3'

    direct = plan_recording('u', 'd/a', 'MP4', direct_flv_url='https://x/a.flv')
    assert direct.is_direct and direct.output_path == 'd/a.flv'


def test_produced_files_finds_segments(tmp_path):
    base = tmp_path / 'a[1]_2024'
    plan = plan_recording('u', str(base), 'TS', split=True)
    for i in range(3):
        (tmp_path / f'a[1]_2024_{i:03d}.ts').write_bytes(b'x')
    (tmp_path / 'other_000.ts').write_bytes(b'x')
    assert [p.name for p in plan.produced_files()] == ['a[1]_2024_000.ts', 'a[1]_2024_001.ts', 'a[1]_2024_002.ts']


def test_build_save_dir():
    assert build_save_dir('D:/rec', '抖音直播', '主播', '2024-01-01', '标题', True, True, True) == \
        'D:/rec/抖音直播/主播/2024-01-01/标题_主播'
    assert build_save_dir('D:/rec', '抖音直播', '主播', '2024-01-01', '标题', False, False, True) == \
        'D:/rec/抖音直播/2024-01-01_标题'
