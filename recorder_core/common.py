# -*- coding: utf-8 -*-
import os
import re
import time
from pathlib import Path

from src.utils import remove_emojis

TEXT_ENCODING = 'utf-8-sig'

QUALITY_CHOICES = ("原画", "蓝光", "超清", "高清", "标清", "流畅")
QUALITY_CODES = {
    "原画": "OD",
    "蓝光": "BD",
    "超清": "UHD",
    "高清": "HD",
    "标清": "SD",
    "流畅": "LD",
}

_NAME_FORBIDDEN = r"[\/\\\:\*\？?\"\<\>\|&#.。,， ~！· ]"


def atomic_write_text(path: Path, text: str, retries: int = 20) -> None:
    """Write via a temp file and rename.

    On Windows the rename fails while another thread or process (backup, antivirus, an editor)
    has the target open, so retry briefly before giving up.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + '.tmp')
    with open(tmp_path, 'w', encoding=TEXT_ENCODING) as f:
        f.write(text)
    for attempt in range(retries):
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(0.05)


def quality_code(quality_zh: str) -> str:
    return QUALITY_CODES.get(quality_zh, "OD")


def clean_name(text: str, strip_emoji: bool = True) -> str:
    cleaned = re.sub(_NAME_FORBIDDEN, "_", (text or '').strip()).strip('_')
    cleaned = cleaned.replace("（", "(").replace("）", ")")
    if strip_emoji:
        cleaned = remove_emojis(cleaned, '_').strip('_')
    return cleaned or '空白昵称'
