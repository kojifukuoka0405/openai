"""data/ からの読み込み。数値は全てここ経由で外部 JSON から来る。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .state import GameState, GlobalKPIs, Nation

# パッケージ（terra_sim/）の親 = mars_sim/、その下の data/
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_nations() -> dict[str, dict[str, Any]]:
    """nations.json を id->定義 で返す。"""
    raw = _read_json(DATA_DIR / "nations" / "nations.json")
    return {n["id"]: n for n in raw["nations"]}


def load_events() -> list[dict[str, Any]]:
    """data/events/*.json を全て読み込み、イベント定義のリストにする。

    1ファイル＝1イベント（dict）でも、複数イベント（list）でも受け付ける。
    """
    events: list[dict[str, Any]] = []
    events_dir = DATA_DIR / "events"
    if not events_dir.exists():
        return events
    for path in sorted(events_dir.glob("*.json")):
        data = _read_json(path)
        if isinstance(data, list):
            events.extend(data)
        else:
            events.append(data)
    return events


def load_missions() -> dict[str, Any]:
    """ミッション・テンプレートと火星窓の定義を返す。"""
    return _read_json(DATA_DIR / "missions" / "missions.json")


def build_nation(defn: dict[str, Any], *, is_player: bool) -> Nation:
    """JSON 定義から動的ステータス付きの Nation を生成する。"""
    p = defn["params"]
    pw = p["public_will"]
    pw_base = pw["base"] if isinstance(pw, dict) else float(pw)
    return Nation(
        id=defn["id"],
        name_ja=defn["name_ja"],
        is_player=is_player,
        default_doctrine=defn.get("default_doctrine", "balanced"),
        budget=float(p["budget"]),
        budget_base=float(p["budget"]),
        capability=float(p["capability"]),
        public_will=float(pw_base),
        cohesion_affinity=float(p["cohesion_affinity"]),
        commercial=float(p["commercial"]),
        # 他国の対自国信頼は協調親和を初期値に
        trust_to_player=float(p["cohesion_affinity"]),
    )


def new_game(player_id: str, *, start_year: int = 2035) -> GameState:
    """新規ゲーム状態を構築する。"""
    defs = load_nations()
    if player_id not in defs:
        raise ValueError(f"未知の国: {player_id}（選択肢: {', '.join(defs)}）")
    nations = {
        nid: build_nation(defn, is_player=(nid == player_id))
        for nid, defn in defs.items()
    }
    return GameState(
        year=start_year,
        turn=0,
        kpi=GlobalKPIs(),
        nations=nations,
        player_id=player_id,
    )
