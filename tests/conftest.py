# -*- coding: utf-8 -*-
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('DLR_SKIP_AUTO_INSTALL', '1')
sys.argv[0] = str(ROOT / 'pytest_runner')
sys.path.insert(0, str(ROOT))


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / 'config').mkdir()
    example = (ROOT / 'config' / 'config.ini.example').read_text(encoding='utf-8-sig')
    (tmp_path / 'config' / 'config.ini.example').write_text(example, encoding='utf-8-sig')
    return tmp_path
