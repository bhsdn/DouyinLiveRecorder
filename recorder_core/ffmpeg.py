# -*- coding: utf-8 -*-
from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = ("Mozilla/5.0 (Linux; Android 11; SAMSUNG SM-G973U) AppleWebKit/537.36 ("
              "KHTML, like Gecko) SamsungBrowser/14.2 Chrome/87.0.4280.141 Mobile "
              "Safari/537.36")
PROTOCOL_WHITELIST = "rtmp,crypto,file,http,https,tcp,tls,udp,rtp,httpproxy"

AUDIO_TYPES = ("MP3", "M4A")
EXTENSIONS = {"TS": "ts", "FLV": "flv", "MKV": "mkv", "MP4": "mp4", "MP3": "mp3", "M4A": "m4a"}


def normalize_save_type(save_type: str) -> str:
    save_type = (save_type or 'TS').upper()
    if 'MP3' in save_type:
        return 'MP3'
    if 'M4A' in save_type:
        return 'M4A'
    return save_type if save_type in EXTENSIONS else 'TS'


@dataclass
class RecordPlan:
    save_type: str
    output_path: str
    command: list[str] | None = None
    direct_url: str | None = None
    segmented: bool = False

    @property
    def is_direct(self) -> bool:
        return self.command is None

    @property
    def is_audio(self) -> bool:
        return self.save_type in AUDIO_TYPES

    def produced_files(self) -> list[Path]:
        if '%03d' in self.output_path:
            pattern = glob.escape(self.output_path.replace('%03d', '@@@')).replace('@@@', '[0-9][0-9][0-9]')
            return sorted(Path(p) for p in glob.glob(pattern))
        path = Path(self.output_path)
        return [path] if path.exists() else []


def input_args(real_url: str, proxy: str | None = None, header: str | None = None,
               overseas: bool = False) -> list[str]:
    if overseas:
        rw_timeout, analyzeduration, probesize, bufsize, muxing_queue = (
            "50000000", "40000000", "20000000", "15000k", "2048")
    else:
        rw_timeout, analyzeduration, probesize, bufsize, muxing_queue = (
            "15000000", "20000000", "10000000", "8000k", "1024")

    command = ['ffmpeg']
    if proxy:
        command += ["-http_proxy", proxy if proxy.startswith('http') else 'http://' + proxy]
    command += [
        "-y",
        "-v", "verbose",
        "-rw_timeout", rw_timeout,
        "-loglevel", "error",
        "-hide_banner",
        "-user_agent", USER_AGENT,
    ]
    if header:
        command += ["-headers", header]
    command += [
        "-protocol_whitelist", PROTOCOL_WHITELIST,
        "-thread_queue_size", "1024",
        "-analyzeduration", analyzeduration,
        "-probesize", probesize,
        "-fflags", "+discardcorrupt",
        "-re", "-i", real_url,
        "-bufsize", bufsize,
        "-sn", "-dn",
        "-reconnect_delay_max", "60",
        "-reconnect_streamed", "-reconnect_at_eof",
        "-max_muxing_queue_size", muxing_queue,
        "-correct_ts_overflow", "1",
        "-avoid_negative_ts", "1",
    ]
    return command


def output_args(save_type: str, output_path: str, split: bool, split_time: int | str) -> list[str]:
    split_time = str(split_time)
    segment = ["-f", "segment", "-segment_time", split_time]

    if save_type == 'MP3':
        args = ["-map", "0:a", "-c:a", "libmp3lame", "-ab", "320k"]
        if split:
            args += segment + ["-reset_timestamps", "1"]
    elif save_type == 'M4A':
        args = ["-map", "0:a", "-c:a", "aac", "-bsf:a", "aac_adtstoasc", "-ab", "320k"]
        if split:
            args += segment + ["-segment_format", "mpegts", "-reset_timestamps", "1"]
        else:
            args += ["-movflags", "+faststart"]
    elif save_type == 'FLV':
        args = ["-map", "0", "-c:v", "copy", "-c:a", "copy", "-bsf:a", "aac_adtstoasc"]
        if split:
            args += segment + ["-segment_format", "flv", "-reset_timestamps", "1"]
        else:
            args += ["-f", "flv"]
    elif save_type == 'MKV':
        if split:
            args = ["-flags", "global_header", "-c:v", "copy", "-c:a", "aac", "-map", "0"]
            args += segment + ["-segment_format", "matroska", "-reset_timestamps", "1"]
        else:
            args = ["-flags", "global_header", "-map", "0", "-c:v", "copy", "-c:a", "copy", "-f", "matroska"]
    elif save_type == 'MP4':
        if split:
            args = ["-c:v", "copy", "-c:a", "aac", "-map", "0"]
            args += segment + ["-segment_format", "mp4", "-reset_timestamps", "1",
                               "-movflags", "+frag_keyframe+empty_moov"]
        else:
            args = ["-map", "0", "-c:v", "copy", "-c:a", "copy", "-f", "mp4"]
    else:
        args = ["-c:v", "copy", "-c:a", "copy", "-map", "0"]
        if split:
            args += segment + ["-segment_format", "mpegts", "-reset_timestamps", "1"]
        else:
            args += ["-f", "mpegts"]
    return args + [output_path]


def plan_recording(
        real_url: str,
        base_path: str,
        save_type: str,
        split: bool = False,
        split_time: int | str = 1800,
        proxy: str | None = None,
        header: str | None = None,
        overseas: bool = False,
        audio_only: bool = False,
        direct_flv_url: str | None = None,
) -> RecordPlan:
    """Build the ffmpeg command for one recording.

    base_path is the output path without extension, e.g. downloads/抖音直播/主播/主播_2024-01-01_12-00-00.
    When direct_flv_url is given the stream is downloaded with HTTP instead of ffmpeg.
    """
    save_type = normalize_save_type(save_type)
    if audio_only and save_type not in AUDIO_TYPES:
        save_type = 'MP3'

    if direct_flv_url:
        return RecordPlan(save_type='FLV', output_path=f'{base_path}.flv', direct_url=direct_flv_url)

    extension = EXTENSIONS[save_type]
    output_path = f'{base_path}_%03d.{extension}' if split else f'{base_path}.{extension}'
    command = input_args(real_url, proxy, header, overseas) + output_args(save_type, output_path, split, split_time)
    return RecordPlan(save_type=save_type, output_path=output_path, command=command, segmented=split)


def build_save_dir(root: str, platform_name: str, anchor_name: str, date_str: str, title: str | None,
                   by_author: bool, by_time: bool, by_title: bool) -> str:
    full_path = os.path.join(root, platform_name).replace('\\', '/')
    if by_author:
        full_path = f'{full_path}/{anchor_name}'
    if by_time:
        full_path = f'{full_path}/{date_str}'
    if by_title and title:
        if by_time:
            full_path = f'{full_path}/{title}_{anchor_name}'
        else:
            full_path = f'{full_path}/{date_str}_{title}'
    return full_path
