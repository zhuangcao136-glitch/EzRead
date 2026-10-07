"""Canonical EzRead configuration and the isolated read-only legacy aliases."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Mapping

VERSION = '3.4.1'
_LEGACY_ENV = {
    'EZREAD_DATA_DIR': 'TUDU_DATA_DIR',
    'EZREAD_CODEX_PATH': 'TUDU_CODEX_PATH',
    'EZREAD_CODEX_MODEL': 'TUDU_CODEX_MODEL',
}


def environment_value(name: str, default: str = '', environment: Mapping[str, str] | None = None) -> str:
    env = os.environ if environment is None else environment
    # An explicitly set new key wins, including an intentionally empty value.
    if name in env:
        return env[name]
    previous = _LEGACY_ENV.get(name)
    return env.get(previous, default) if previous else default


def data_directory(root: Path, environment: Mapping[str, str] | None = None) -> Path:
    value = environment_value('EZREAD_DATA_DIR', environment=environment)
    directory = Path(value) if value.strip() else root / 'data'
    if not directory.is_absolute():
        directory = root / directory
    return directory.resolve()
