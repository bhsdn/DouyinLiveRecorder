# -*- encoding: utf-8 -*-

"""
Author: Hmily
GitHub: https://github.com/ihmily
Date: 2023-07-17 23:52:05
Copyright (c) 2023-2025 by Hmily, All Rights Reserved.
Function: Record live stream video (command line front-end of recorder_core).

Usage is unchanged: put one live room URL per line in config/URL_config.ini, prefix a line
with '#' to stop monitoring it, and tune config/config.ini. The desktop GUI lives in gui.py
and the web dashboard in web_server.py; all three share recorder_core.
"""
import builtins
import datetime
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from ffmpeg_install import check_ffmpeg
from recorder_core import VERSION
from recorder_core.common import TEXT_ENCODING
from recorder_core.engine import RecorderEngine
from recorder_core.events import Event
from recorder_core.paths import AppPaths
from recorder_core.worker import RoomState
from src.logger import logger
from src.utils import Color

version = VERSION
platforms = ("\n国内站点：抖音|快手|虎牙|斗鱼|YY|B站|小红书|bigo|blued|网易CC|千度热播|猫耳FM|Look|TwitCasting|"
             "百度|微博|酷狗|花椒|流星|Acfun|畅聊|映客|音播|知乎|嗨秀|VV星球|17Live|浪Live|漂漂|六间房|乐嗨|花猫|淘宝|京东|"
             "咪咕|连接|来秀"
             "\n海外站点：TikTok|SOOP|PandaTV|WinkTV|FlexTV|PopkonTV|TwitchTV|LiveMe|ShowRoom|CHZZK|Shopee|"
             "Youtube|Faceit|Picarto")

color_obj = Color()
clear_command = "cls" if os.name == 'nt' else "clear"


def print_banner() -> None:
    print("-----------------------------------------------------")
    print("|                DouyinLiveRecorder                 |")
    print("-----------------------------------------------------")
    print(f"版本号: {version}")
    print("GitHub: https://github.com/ihmily/DouyinLiveRecorder")
    print(f'支持平台: {platforms}')
    print('.....................................................')


def check_ffmpeg_existence() -> bool:
    try:
        result = subprocess.run(['ffmpeg', '-version'], check=True, capture_output=True, text=True)
        lines = result.stdout.splitlines()
        print(lines[0])
        print(lines[1])
    except subprocess.CalledProcessError as e:
        logger.error(e)
    except (FileNotFoundError, IndexError):
        pass
    if check_ffmpeg():
        time.sleep(1)
        return True
    return False


def setup_language(language: str) -> None:
    if language and 'en' not in language.lower():
        from i18n import translated_print
        builtins.print = translated_print


def ensure_url_config(path: Path) -> None:
    if path.exists() and path.read_text(encoding=TEXT_ENCODING, errors='ignore').strip():
        return
    input_url = input('请输入要录制的主播直播间网址（尽量使用PC网页端的直播间地址）:\n')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(input_url.strip() + '\n', encoding=TEXT_ENCODING)


class ConsoleView:
    """Prints engine log events and redraws the status panel every 5 seconds, like the original program."""

    def __init__(self, engine: RecorderEngine):
        self.engine = engine
        self._stop = threading.Event()
        engine.bus.subscribe(self.on_event)

    def on_event(self, event: Event) -> None:
        if event.type != 'log':
            return
        level, message = event.data['level'], event.data['message']
        if level in ('warning', 'error'):
            return
        if '已被注释' in message or '移除' in message or '瞬时错误' in message:
            color_obj.print_colored(f'\r{message}', color_obj.YELLOW)
        else:
            print(f'\r{message}')

    def run(self) -> None:
        time.sleep(5)
        while not self._stop.wait(5):
            try:
                self.render()
            except Exception as e:
                logger.error(f"错误信息: {e} 发生错误的行数: {e.__traceback__.tb_lineno}")

    def render(self) -> None:
        engine, s = self.engine, self.engine.settings
        statuses = engine.snapshot()
        monitoring = sum(1 for st in statuses if st['enabled'])
        recording = [st for st in statuses if st['state'] == RoomState.RECORDING.value]

        sys.stdout.flush()
        if Path(sys.executable).name != 'pythonw.exe':
            os.system(clear_command)
        print(f"\r共监测{monitoring}个直播中", end=" | ")
        print(f"同一时间访问网络的线程数: {engine.semaphore.limit}", end=" | ")
        print(f"是否开启代理录制: {'是' if s.use_proxy else '否'}", end=" | ")
        print(f"录制分段开启: {str(s.split_time) + '秒' if s.split_enabled else '否'}", end=" | ")
        if s.create_time_file:
            print("是否生成时间文件: 是", end=" | ")
        print(f"录制视频质量为: {s.quality}", end=" | ")
        print(f"录制视频格式为: {s.save_format_code}", end=" | ")
        print(f"目前瞬时错误数为: {engine.error_count}", end=" | ")
        print(f"当前时间: {time.strftime('%H:%M:%S', time.localtime())}")

        if not recording:
            if monitoring == 0:
                print("\r没有正在监测和录制的直播")
            else:
                print(f"\r没有正在录制的直播 循环监测间隔时间：{s.loop_interval}秒")
        else:
            print("x" * 60)
            print(f"正在录制{len(recording)}个直播: ")
            for st in recording:
                elapsed = datetime.timedelta(seconds=st['duration'])
                print(f"序号{st['index']} {st['name']}[{st['quality']}] 正在录制中 {elapsed}")
            print("x" * 60)

        if s.show_loop_time:
            now = time.time()
            for st in statuses:
                if st['next_check']:
                    print(f"\r{st['name'] or st['url']} 循环等待{int(st['next_check'] - now)}秒")

    def stop(self) -> None:
        self._stop.set()


def _raise_system_exit(_signal, _frame):
    raise SystemExit(0)


def main() -> None:
    paths = AppPaths.default()
    print_banner()
    if not check_ffmpeg_existence():
        logger.error("缺少ffmpeg无法进行录制，程序退出")
        sys.exit(1)
    paths.config_dir.mkdir(parents=True, exist_ok=True)

    engine = RecorderEngine(paths)
    setup_language(engine.settings.language)

    free_gb, _ = engine.disk_usage()
    print(f"{engine.save_root} Free: {free_gb:.2f} GB\n")
    if free_gb < float(engine.settings.disk_space_limit):
        logger.warning(f"Disk space remaining is below {engine.settings.disk_space_limit} GB. "
                       f"Exiting program due to the disk space limit being reached.")
        sys.exit(-1)

    ensure_url_config(paths.url_config_file)

    view = ConsoleView(engine)
    signal.signal(signal.SIGTERM, _raise_system_exit)
    engine.start()
    threading.Thread(target=view.run, daemon=True, name='console-view').start()

    exit_code = 0
    try:
        while True:
            time.sleep(1)
            if engine.disk_full and not any(st['state'] == RoomState.RECORDING.value for st in engine.snapshot()):
                logger.warning(f"Disk space remaining is below {engine.settings.disk_space_limit} GB. "
                               f"Exiting program due to the disk space limit being reached.")
                exit_code = -1
                break
    except (KeyboardInterrupt, SystemExit):
        color_obj.print_colored("\n正在停止录制并保存文件，请稍候...", color_obj.YELLOW)
    finally:
        view.stop()
        engine.shutdown()
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
