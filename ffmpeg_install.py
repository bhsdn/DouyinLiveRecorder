# -*- coding: utf-8 -*-

"""
Author: Hmily
GitHub: https://github.com/ihmily
Copyright (c) 2024 by Hmily, All Rights Reserved.
"""

import os
import re
import subprocess
import sys
import platform
import zipfile
from pathlib import Path
import requests
from tqdm import tqdm
from src.logger import logger

current_platform = platform.system()
execute_dir = os.path.split(os.path.realpath(sys.argv[0]))[0]
current_env_path = os.environ.get('PATH')
ffmpeg_path = os.path.join(execute_dir, 'ffmpeg')


def unzip_file(zip_path: str | Path, extract_to: str | Path, delete: bool = True) -> None:
    if not os.path.exists(extract_to):
        os.makedirs(extract_to)

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_to)

    if delete and os.path.exists(zip_path):
        os.remove(zip_path)


def get_lanzou_download_link(url: str, password: str | None = None) -> str | None:
    try:
        headers = {
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'Origin': 'https://wweb.lanzouv.com',
            'Referer': 'https://wweb.lanzouv.com/iXncv0dly6mh',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0',
        }
        response = requests.get(url, headers=headers)
        match = re.search("var skdklds = '(.*?)';", response.text)
        if not match:
            logger.warning("Failed to parse the lanzou share page, trying other download sources")
            return None
        sign = match.group(1)
        data = {
            'action': 'downprocess',
            'sign': sign,
            'p': password,
            'kd': '1',
        }
        response = requests.post('https://wweb.lanzouv.com/ajaxm.php', headers=headers, data=data)
        json_data = response.json()
        download_url = json_data['dom'] + "/file/" + json_data['url']
        response = requests.get(download_url, headers=headers)
        return response.url
    except Exception as e:
        logger.error(f"Failed to obtain ffmpeg download address. {e}")


# Tried in order after the lanzou mirror; both publish Windows builds whose zip contains */bin/ffmpeg.exe.
WINDOWS_FFMPEG_SOURCES = (
    ('GitHub BtbN', 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/'
                    'ffmpeg-master-latest-win64-gpl.zip'),
    ('gyan.dev', 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'),
)


def download_file(url: str, save_path: str | Path, desc: str) -> None:
    save_path = Path(save_path)
    tmp_path = save_path.with_suffix(save_path.suffix + '.part')
    with requests.get(url, stream=True, timeout=30) as response:
        response.raise_for_status()
        total_size = int(response.headers.get('Content-Length', 0))
        with tqdm(total=total_size, unit="B", unit_scale=True, ncols=100, desc=desc) as t:
            with open(tmp_path, 'wb') as f:
                for data in response.iter_content(1024 * 256):
                    t.update(len(data))
                    f.write(data)
    os.replace(tmp_path, save_path)


def extract_ffmpeg_binaries(zip_path: str | Path, target_dir: str | Path) -> bool:
    """Copy ffmpeg.exe / ffprobe.exe from a build zip into target_dir, ignoring the zip's folder layout."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            name = member.rsplit('/', 1)[-1].lower()
            if name in ('ffmpeg.exe', 'ffprobe.exe'):
                with zf.open(member) as src, open(target_dir / name, 'wb') as dst:
                    dst.write(src.read())
    return (target_dir / 'ffmpeg.exe').exists()


def ffmpeg_works() -> bool:
    if ffmpeg_path not in os.environ.get('PATH', '').split(os.pathsep):
        os.environ['PATH'] = ffmpeg_path + os.pathsep + os.environ.get('PATH', '')
    try:
        return subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode == 0
    except OSError:
        return False


def install_ffmpeg_from_lanzou() -> bool:
    ffmpeg_url = get_lanzou_download_link('https://wweb.lanzouv.com/iHAc22ly3r3g', 'eots')
    if not ffmpeg_url:
        return False
    zip_file_path = Path(execute_dir) / 'ffmpeg_latest_build_20250124.zip'
    if zip_file_path.exists():
        logger.debug("ffmpeg installation file already exists, start install...")
    else:
        download_file(ffmpeg_url, zip_file_path, 'Downloading ffmpeg (v20250124)')
    unzip_file(zip_file_path, execute_dir)
    return ffmpeg_works()


def install_ffmpeg_windows() -> bool:
    logger.warning("ffmpeg is not installed.")
    logger.debug("Installing the latest version of ffmpeg for Windows...")
    try:
        if install_ffmpeg_from_lanzou():
            logger.debug('ffmpeg installation was successful')
            return True
    except Exception as e:
        logger.warning(f"Download from lanzou failed: {type(e).__name__}: {e}")

    for name, url in WINDOWS_FFMPEG_SOURCES:
        zip_file_path = Path(execute_dir) / 'ffmpeg_download.zip'
        try:
            logger.debug(f"Downloading ffmpeg from {name}: {url}")
            download_file(url, zip_file_path, f'Downloading ffmpeg ({name})')
            if extract_ffmpeg_binaries(zip_file_path, ffmpeg_path) and ffmpeg_works():
                logger.debug(f'ffmpeg installation was successful ({name})')
                return True
            logger.warning(f"ffmpeg from {name} could not be started")
        except Exception as e:
            logger.warning(f"Download from {name} failed: {type(e).__name__}: {e}")
        finally:
            zip_file_path.unlink(missing_ok=True)

    logger.error("ffmpeg installation failed. Please manually install ffmpeg by yourself")
    return False


def install_ffmpeg_mac():
    logger.warning("ffmpeg is not installed.")
    logger.debug("Installing the stable version of ffmpeg for macOS...")
    try:
        result = subprocess.run(["brew", "install", "ffmpeg"], capture_output=True)
        if result.returncode == 0:
            logger.debug('ffmpeg installation was successful. Restart for changes to take effect.')
            return True
        else:
            logger.error("ffmpeg installation failed")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to install ffmpeg using Homebrew. {e}")
        logger.error("Please install ffmpeg manually or check your Homebrew installation.")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")


def install_ffmpeg_linux():
    is_RHS = True

    try:
        logger.warning("ffmpeg is not installed.")
        logger.debug("Trying to install the stable version of ffmpeg")
        result = subprocess.run(['yum', '-y', 'update'], capture_output=True)
        if result.returncode != 0:
            logger.error("Failed to update package lists using yum.")
            return False

        result = subprocess.run(['yum', 'install', '-y', 'ffmpeg'], capture_output=True)
        if result.returncode == 0:
            logger.debug("ffmpeg installation was successful using yum. Restart for changes to take effect.")
            return True
        logger.error(result.stderr.decode('utf-8').strip())
    except FileNotFoundError:
        logger.debug("yum command not found, trying to install using apt...")
        is_RHS = False
    except Exception as e:
        logger.error(f"An error occurred while trying to install ffmpeg using yum: {e}")

    if not is_RHS:
        try:
            logger.debug("Trying to install the stable version of ffmpeg for Linux using apt...")
            result = subprocess.run(['apt', 'update'], capture_output=True)
            if result.returncode != 0:
                logger.error("Failed to update package lists using apt")
                return False

            result = subprocess.run(['apt', 'install', '-y', 'ffmpeg'], capture_output=True)
            if result.returncode == 0:
                logger.debug("ffmpeg installation was successful using apt. Restart for changes to take effect.")
                return True
            else:
                logger.error(result.stderr.decode('utf-8').strip())
        except FileNotFoundError:
            logger.error("apt command not found, unable to install ffmpeg. Please manually install ffmpeg by yourself")
        except Exception as e:
            logger.error(f"An error occurred while trying to install ffmpeg using apt: {e}")
    logger.error("Manual installation of ffmpeg is required. Please manually install ffmpeg by yourself.")
    return False


def install_ffmpeg() -> bool:
    if current_platform == "Windows":
        return install_ffmpeg_windows()
    elif current_platform == "Linux":
        return install_ffmpeg_linux()
    elif current_platform == "Darwin":
        return install_ffmpeg_mac()
    else:
        logger.debug(f"ffmpeg auto installation is not supported on this platform: {current_platform}. "
                     f"Please install ffmpeg manually.")
    return False


def ensure_ffmpeg_installed(func):
    def wrapper(*args, **kwargs):
        try:
            result = subprocess.run(['ffmpeg', '-version'], capture_output=True)
            version = result.stdout.strip()
            if result.returncode == 0 and version:
                return func(*args, **kwargs)
        except FileNotFoundError:
            pass
        return False

    def wrapped_func(*args, **kwargs):
        if sys.version_info >= (3, 7):
            res = wrapper(*args, **kwargs)
        else:
            res = wrapper(*args, **kwargs)
        if not res:
            install_ffmpeg()
            res = wrapper(*args, **kwargs)

        if not res:
            raise RuntimeError("ffmpeg is not installed.")

        return func(*args, **kwargs)

    return wrapped_func


def check_ffmpeg_installed() -> bool:
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True)
        version = result.stdout.strip()
        if result.returncode == 0 and version:
            return True
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"OSError occurred: {e}. ffmpeg may not be installed correctly or is not available in the system PATH.")
        print("Please delete the ffmpeg and try to download and install again.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    return False


def check_ffmpeg() -> bool:
    if not check_ffmpeg_installed():
        return install_ffmpeg()
    return True
