"""API キーの読み書き。

優先順位は 環境変数 `OPENAI_API_KEY` → 設定ファイル。
設定ファイルはユーザーのホーム配下に、本人だけが読める権限（600）で保存する。
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path.home() / ".audio_transcriber"
CONFIG_PATH = CONFIG_DIR / "config.json"


def load_config() -> dict:
    """設定ファイルを読む。無ければ空の辞書。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(data: dict) -> Path:
    """設定ファイルを書く（本人のみ読み書き可）。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    try:
        os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover - Windows など
        pass
    return CONFIG_PATH


def resolve_api_key(explicit: Optional[str] = None) -> Optional[str]:
    """使用する API キーを決める。"""
    for candidate in (explicit, os.environ.get("OPENAI_API_KEY"), load_config().get("api_key")):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def save_api_key(api_key: str) -> Path:
    """API キーを設定ファイルに保存する。"""
    data = load_config()
    data["api_key"] = api_key.strip()
    return save_config(data)


def forget_api_key() -> None:
    """保存済みの API キーを削除する。"""
    data = load_config()
    if "api_key" in data:
        data.pop("api_key")
        save_config(data)
