# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import signal
import subprocess
import threading
from collections import deque
from typing import Callable

import httpx

from .postprocess import startup_info

LineCallback = Callable[[str], None]


class ProcessRunner:
    """Runs one recording. Replaceable in tests."""

    def run(self, command: list[str], stop_event: threading.Event, on_line: LineCallback | None = None) -> int:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            startupinfo=startup_info(),
        )
        tail: deque[str] = deque(maxlen=20)

        def read_stderr():
            for raw in iter(process.stderr.readline, b''):
                line = raw.decode('utf-8', errors='replace').rstrip()
                if line:
                    tail.append(line)
                    if on_line:
                        on_line(line)

        reader = threading.Thread(target=read_stderr, daemon=True, name='ffmpeg-stderr')
        reader.start()

        while process.poll() is None:
            if stop_event.wait(1):
                self._graceful_stop(process)
                break
        process.wait()
        reader.join(timeout=2)
        return process.returncode

    @staticmethod
    def _graceful_stop(process: subprocess.Popen) -> None:
        try:
            if os.name == 'nt':
                if process.stdin:
                    process.stdin.write(b'q')
                    process.stdin.flush()
                    process.stdin.close()
            else:
                process.send_signal(signal.SIGINT)
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
        except (OSError, ValueError):
            if process.poll() is None:
                process.kill()

    def download(self, url: str, save_path: str, headers: dict[str, str], stop_event: threading.Event,
                 proxy: str | None = None) -> bool:
        if proxy and not proxy.startswith('http'):
            proxy = 'http://' + proxy
        with httpx.Client(timeout=None, proxy=proxy) as client:
            with client.stream('GET', url, headers=headers, follow_redirects=True) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"请求直播流失败，状态码: {response.status_code}")
                with open(save_path, 'wb') as f:
                    for chunk in response.iter_bytes(1024 * 16):
                        if stop_event.is_set():
                            return False
                        if chunk:
                            f.write(chunk)
        return True
