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


def stored_keys() -> dict:
    """設定ファイルに保存されているプロバイダ別のキー。"""
    data = load_config()
    keys = dict(data.get("api_keys") or {})
    legacy = data.get("api_key")          # 旧形式（OpenAI のみ）
    if legacy and "openai" not in keys:
        keys["openai"] = legacy
    return {k: v for k, v in keys.items() if isinstance(v, str) and v.strip()}


def resolve_api_key(explicit: Optional[str] = None, provider: str = "openai",
                    env_var: Optional[str] = None) -> Optional[str]:
    """使用する API キーを決める（明示指定 → 環境変数 → 設定ファイル）。"""
    env_var = env_var or ("OPENAI_API_KEY" if provider == "openai" else f"{provider.upper()}_API_KEY")
    for candidate in (explicit, os.environ.get(env_var), stored_keys().get(provider)):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def save_api_key(api_key: str, provider: str = "openai") -> Path:
    """API キーを設定ファイルに保存する。"""
    data = load_config()
    keys = dict(data.get("api_keys") or {})
    keys[provider] = api_key.strip()
    data["api_keys"] = keys
    if provider == "openai":
        data["api_key"] = api_key.strip()   # 旧形式との互換
    return save_config(data)


def forget_api_key(provider: str = "openai") -> None:
    """保存済みの API キーを削除する。"""
    data = load_config()
    keys = dict(data.get("api_keys") or {})
    changed = keys.pop(provider, None) is not None
    data["api_keys"] = keys
    if provider == "openai" and "api_key" in data:
        data.pop("api_key")
        changed = True
    if changed:
        save_config(data)
