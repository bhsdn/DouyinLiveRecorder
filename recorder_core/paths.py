# -*- coding: utf-8 -*-
import os
import sys
from dataclasses import dataclass
from pathlib import Path


def get_base_dir() -> Path:
    env_home = os.environ.get('DLR_HOME')
    if env_home:
        return Path(env_home).resolve()
    return Path(os.path.split(os.path.realpath(sys.argv[0]))[0])


@dataclass(frozen=True)
class AppPaths:
    base: Path

    @classmethod
    def default(cls) -> "AppPaths":
        return cls(get_base_dir())

    @property
    def config_dir(self) -> Path:
        return self.base / 'config'

    @property
    def config_file(self) -> Path:
        return self.config_dir / 'config.ini'

    @property
    def example_config_file(self) -> Path:
        return self.config_dir / 'config.ini.example'

    @property
    def url_config_file(self) -> Path:
        return self.config_dir / 'URL_config.ini'

    @property
    def backup_dir(self) -> Path:
        return self.base / 'backup_config'

    @property
    def default_download_dir(self) -> Path:
        return self.base / 'downloads'

    @property
    def logs_dir(self) -> Path:
        return self.base / 'logs'
