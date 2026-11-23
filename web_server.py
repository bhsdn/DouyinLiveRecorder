# -*- coding: utf-8 -*-
"""Web dashboard entry point.

    python web_server.py [--host 0.0.0.0] [--port 8686] [--password xxx]

Defaults come from the [Web] section of config/config.ini. Environment variables
DLR_WEB_HOST / DLR_WEB_PORT / DLR_WEB_PASSWORD override both (handy for Docker).
Listening on anything other than localhost requires a password.
"""
import argparse
import os
import sys

os.environ.setdefault('DLR_SKIP_AUTO_INSTALL', '1')

# pythonw / Windows services have no console streams, but src/logger.py registers sys.stderr as a sink.
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description='DouyinLiveRecorder Web 管理端')
    parser.add_argument('--host', default=os.environ.get('DLR_WEB_HOST'))
    parser.add_argument('--port', type=int, default=int(os.environ['DLR_WEB_PORT'])
                        if os.environ.get('DLR_WEB_PORT') else None)
    parser.add_argument('--password', default=os.environ.get('DLR_WEB_PASSWORD'))
    args = parser.parse_args()

    import uvicorn

    from ffmpeg_install import check_ffmpeg_installed
    from recorder_core.engine import RecorderEngine, ensure_tool_paths
    from src.initializer import check_node
    from web.server import create_app, is_loopback

    ensure_tool_paths()
    engine = RecorderEngine()
    host = args.host or engine.settings.web_host or '127.0.0.1'
    port = args.port or int(engine.settings.web_port or 8686)
    password = args.password if args.password else None
    if not is_loopback(host) and not (password or engine.settings.web_password):
        print(f'错误：监听 {host} 会允许其他设备访问，必须先设置访问密码 '
              f'(config.ini [Web] 访问密码，或 --password / DLR_WEB_PASSWORD)。')
        return 2

    if not check_ffmpeg_installed():
        print('警告：未检测到 ffmpeg，无法录制。请安装 ffmpeg 或运行 python main.py 自动安装。')
    check_node()

    app = create_app(engine, password_override=password)
    print(f'Web 管理端已启动: http://{"127.0.0.1" if host in ("0.0.0.0", "::") else host}:{port}')
    uvicorn.run(app, host=host, port=port, log_level='warning')
    return 0


if __name__ == '__main__':
    sys.exit(main())
