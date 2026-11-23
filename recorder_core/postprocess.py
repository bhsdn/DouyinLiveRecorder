# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from src.logger import logger

from .common import TEXT_ENCODING

LogFn = Callable[[str, str], None]


def startup_info():
    if os.name == 'nt':
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return info
    return None


def _run_ffmpeg(command: list[str]) -> None:
    subprocess.check_output(command, stderr=subprocess.STDOUT, startupinfo=startup_info())


def _remove_after(path: Path) -> None:
    time.sleep(1)
    if path.exists():
        path.unlink()


def convert_to_mp4(path: str | Path, reencode_h264: bool = False, delete_origin: bool = True) -> Path | None:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0 or path.suffix.lower() == '.mp4':
        return None
    target = path.with_suffix('.mp4')
    if reencode_h264:
        command = ["ffmpeg", "-y", "-i", str(path), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                   "-vf", "format=yuv420p", "-c:a", "copy", "-f", "mp4", str(target)]
    else:
        command = ["ffmpeg", "-y", "-i", str(path), "-c:v", "copy", "-c:a", "copy", "-f", "mp4", str(target)]
    try:
        _run_ffmpeg(command)
        if delete_origin:
            _remove_after(path)
        return target
    except subprocess.CalledProcessError as e:
        logger.error(f'Error occurred during conversion: {e}')
    except Exception as e:
        logger.error(f'An unknown error occurred: {e}')
    return None


def convert_to_m4a(path: str | Path, delete_origin: bool = True) -> Path | None:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return None
    target = path.with_suffix('.m4a')
    try:
        _run_ffmpeg(["ffmpeg", "-i", str(path), "-n", "-vn", "-c:a", "aac", "-bsf:a", "aac_adtstoasc",
                     "-ab", "320k", str(target)])
        if delete_origin:
            _remove_after(path)
        return target
    except subprocess.CalledProcessError as e:
        logger.error(f'Error occurred during conversion: {e}')
    except Exception as e:
        logger.error(f'An unknown error occurred: {e}')
    return None


def convert_files_async(paths: list[Path], reencode_h264: bool, delete_origin: bool,
                        on_done: Callable[[Path, Path | None], None] | None = None) -> threading.Thread:
    def run():
        for p in paths:
            result = convert_to_mp4(p, reencode_h264, delete_origin)
            if on_done:
                on_done(p, result)

    thread = threading.Thread(target=run, daemon=True, name='convert-mp4')
    thread.start()
    return thread


class SubtitleWriter(threading.Thread):
    """Writes one srt cue per second containing the wall-clock time, until stopped."""

    def __init__(self, base_path: str, sub_format: str = 'srt'):
        super().__init__(daemon=True, name='subtitles')
        self.file_path = f'{base_path}.{sub_format.lower()}'
        self._stop_event = threading.Event()

    @staticmethod
    def _fmt(seconds: int) -> str:
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def run(self) -> None:
        index = 0
        while not self._stop_event.is_set():
            index += 1
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cue = f"{index}\n{self._fmt(index)},000 --> {self._fmt(index + 1)},000\n{now}\n\n"
            with open(self.file_path, 'a', encoding=TEXT_ENCODING) as f:
                f.write(cue)
            self._stop_event.wait(1)

    def stop(self) -> None:
        self._stop_event.set()


def build_script_command(command: str, record_name: str, save_file_path: str, save_type: str,
                         split: bool, convert_mp4: bool) -> str:
    if "python" in command:
        params = [
            f'--record_name "{record_name}"',
            f'--save_file_path "{save_file_path}"',
            f'--save_type {save_type}',
            f'--split_video_by_time {split}',
            f'--converts_to_mp4 {convert_mp4}',
        ]
    else:
        params = [
            f'"{record_name.split(" ", maxsplit=1)[-1]}"',
            f'"{save_file_path}"',
            save_type,
            f'split_video_by_time:{split}',
            f'converts_to_mp4:{convert_mp4}',
        ]
    return command.strip() + ' ' + ' '.join(params)


def run_script(command: str, log: LogFn | None = None) -> None:
    log = log or (lambda level, msg: print(msg))
    try:
        process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   startupinfo=startup_info())
        stdout, stderr = process.communicate()
        out = stdout.decode('utf-8', errors='replace').strip()
        err = stderr.decode('utf-8', errors='replace').strip()
        if out:
            log('info', out)
        if err:
            log('warning', err)
    except PermissionError as e:
        log('error', f'{e} 脚本无执行权限!, 若是Linux环境, 请先执行:chmod +x your_script.sh 授予脚本可执行权限')
    except OSError as e:
        log('error', f'{e} Please add `#!/bin/bash` at the beginning of your bash script file.')
