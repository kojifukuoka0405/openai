"""M5: 可視化用の履歴エクスポート。

決定論エンジンを走らせ、毎ターンのスナップショット＋年代記を JSON 化する。
そのデータを ui/web のフロントエンド（太陽系俯瞰・ダッシュボード・年代記）が再生する。
自己完結 HTML（データ埋め込み）も生成でき、ブラウザで開くだけで鑑賞できる。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .engine import make_observer_engine
from .state import MILESTONES, GameState

WEB_DIR = Path(__file__).resolve().parent.parent / "ui" / "web"

# 国の表示色（フロントエンドと共有）
NATION_COLORS = {"usa": "#5b8def", "china": "#e0524f", "japan": "#46c39a"}


def _nation_frame(n) -> dict[str, Any]:
    return {
        "id": n.id,
        "name": n.name_ja,
        "is_player": n.is_player,
        "budget": round(n.budget, 1),
        "capability": round(n.capability, 1),
        "spacefaring": round(n.spacefaring, 1),
        "treasury": round(n.treasury, 0),
        "risk_debt": round(n.risk_debt, 1),
        "tech_count": len(n.tech),
        "reached": sorted(n.reached),
        "mission": (
            {"dest": n.mission.dest, "kind": n.mission.kind, "stage": n.mission.stage}
            if n.mission else None
        ),
    }


def _frame(state: GameState, mars_window: bool, new_events: list) -> dict[str, Any]:
    k = state.kpi
    return {
        "year": state.year,
        "turn": state.turn,
        "mars_window": mars_window,
        "space_winter": state.space_winter,
        "kpi": {
            "cohesion": round(k.cohesion, 1),
            "public_will": round(k.public_will, 1),
            "knowledge": round(k.knowledge, 1),
            "risk_debt": round(k.risk_debt, 1),
            "mandate": round(k.mandate, 1),
            "spacefaring": round(k.spacefaring, 1),
            "offworld_pop": k.offworld_pop,
            "self_sufficiency": round(k.self_sufficiency, 1),
        },
        "nations": [_nation_frame(n) for n in state.nations.values()],
        "events": [{"year": e.year, "text": e.text, "kind": e.kind} for e in new_events],
    }


def build_run(nation: str, *, seed: int = 7, turns: int = 60,
              fate: str = "balanced", doctrine: str | None = None,
              start_year: int = 2035) -> dict[str, Any]:
    """観測モードを走らせ、再生用の履歴データを構築する。"""
    eng = make_observer_engine(nation, seed=seed, self_doctrine=doctrine,
                               fate_policy=fate, start_year=start_year)
    s = eng.state
    frames = [_frame(s, eng.mars_window(s.year), [])]
    seen = 0
    for _ in range(turns):
        if s.space_winter:
            break
        eng.run_turn()
        new = s.chronicle[seen:]
        seen = len(s.chronicle)
        frames.append(_frame(s, eng.mars_window(s.year), new))
    return {
        "meta": {
            "nation": nation,
            "player_doctrine": doctrine or s.nations[nation].default_doctrine,
            "fate": fate,
            "seed": seed,
            "turns": turns,
            "start_year": start_year,
            "nation_colors": NATION_COLORS,
            "milestones": [{"key": k, "label": lbl} for _t, k, lbl in MILESTONES],
        },
        "frames": frames,
    }


def write_json(run: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")


def write_standalone_html(run: dict[str, Any], out_path: Path,
                          template: Path | None = None) -> None:
    """index.html にデータを埋め込んだ自己完結 HTML を書き出す（ダブルクリックで開ける）。"""
    template = template or (WEB_DIR / "index.html")
    html = template.read_text(encoding="utf-8")
    blob = json.dumps(run, ensure_ascii=False)
    inject = f'<script id="run-data">window.__RUN__ = {blob};</script>'
    # プレースホルダがあれば置換、無ければ </head> 直前に差し込む
    if "<!--__RUN_DATA__-->" in html:
        html = html.replace("<!--__RUN_DATA__-->", inject)
    else:
        html = html.replace("</head>", inject + "\n</head>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
