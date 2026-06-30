"""イベントの効果適用。

events.md のスキーマ語彙（kpi / factions_all / faction_self /
trust_to_player_all / flags / uncertainty）をそのまま解釈する。
新イベントは data/events に JSON を足すだけ（コード改変不要）。
"""

from __future__ import annotations

from typing import Any

from .rng import Rng
from .state import GameState, Nation, clamp


def _apply_kpi(state: GameState, kpi: dict[str, Any]) -> None:
    for key, delta in kpi.items():
        cur = getattr(state.kpi, key, None)
        if cur is None:
            continue
        if key == "offworld_pop":
            setattr(state.kpi, key, max(0, int(cur + delta)))
        else:
            setattr(state.kpi, key, clamp(cur + delta))


def _apply_to_nation(n: Nation, eff: dict[str, Any]) -> None:
    for key, delta in eff.items():
        if key == "budget_pct":
            n.budget = clamp(n.budget * (1 + delta / 100.0), 0, 200)
        elif hasattr(n, key):
            setattr(n, key, clamp(getattr(n, key) + delta, 0, 200))


def apply_effects(state: GameState, effects: dict[str, Any]) -> None:
    """確定効果（uncertainty を除く）を状態へ適用する。"""
    if not effects:
        return
    if "kpi" in effects:
        _apply_kpi(state, effects["kpi"])
    if "factions_all" in effects:
        for n in state.nations.values():
            _apply_to_nation(n, effects["factions_all"])
    if "faction_self" in effects:
        _apply_to_nation(state.player, effects["faction_self"])
    if "trust_to_player_all" in effects:
        delta = effects["trust_to_player_all"]
        for n in state.others:
            n.trust_to_player = clamp(n.trust_to_player + delta, 0, 200)
    if "flags" in effects:
        for k, v in effects["flags"].items():
            state.flags[k] = state.flags.get(k, 0) + v


def resolve_choice(state: GameState, choice: dict[str, Any], rng: Rng) -> list[str]:
    """1つの選択肢を解決：確定効果＋確率分岐（uncertainty）を適用。

    返り値は発生した不確実分岐のラベル一覧（年代記用）。
    """
    notes: list[str] = []
    apply_effects(state, choice.get("effects", {}))
    for branch in choice.get("uncertainty", []) or []:
        if rng.chance(float(branch.get("p", 0.0))):
            apply_effects(state, branch.get("effects", {}))
            if branch.get("label"):
                notes.append(branch["label"])
    return notes


class EventBook:
    """イベント定義の索引。id 引きと、神レイヤーが起動可能な候補の抽出。"""

    def __init__(self, events: list[dict[str, Any]]):
        self.by_id: dict[str, dict[str, Any]] = {e["id"]: e for e in events}

    def get(self, event_id: str) -> dict[str, Any] | None:
        return self.by_id.get(event_id)

    def god_authorable(self, state: GameState) -> list[dict[str, Any]]:
        """いま神が Mandate を払って能動起動できるイベント。"""
        out = []
        for e in self.by_id.values():
            if e.get("layer") != "god":
                continue
            if e.get("mandate_cost", 0) <= state.kpi.mandate:
                out.append(e)
        return out
