"""DecisionProvider 抽象 — 「誰が決めるか」をエンジンから切り離す。

game_design §10 / observer_mode.md の中核要件。エンジンのターンループは
意思決定点でここに問い合わせるだけ。実装を差し替えることで
標準プレイ・観測モード・部分自律が同一エンジン上で連続的に成立する。

- 国家意思決定: national_decision(state, nation) -> NationalDecision
- 神レイヤー  : god_decision(state, book)     -> GodDecision | None
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .events import EventBook
from .rng import Rng
from .state import GameState, Nation


@dataclass
class NationalDecision:
    """その国がこのターン取る方針。"""

    space_ratio: float          # 予算のうち宇宙に振る割合 0..1
    w_rd: float                 # 宇宙予算内訳：研究
    w_missions: float           # 　　　　　　：製造・打ち上げ
    w_safety: float             # 　　　　　　：安全マージン
    diplomacy: bool = False     # 協調アクション（cohesion を育てる）

    def normalized(self) -> "NationalDecision":
        s = self.w_rd + self.w_missions + self.w_safety
        if s <= 0:
            return NationalDecision(self.space_ratio, 1 / 3, 1 / 3, 1 / 3, self.diplomacy)
        return NationalDecision(
            max(0.0, min(1.0, self.space_ratio)),
            self.w_rd / s, self.w_missions / s, self.w_safety / s, self.diplomacy,
        )


@dataclass
class GodDecision:
    event_id: str


# ---- ドクトリン（国家AIの方針プリセット）-----------------------------------
# space_ratio と内訳重み、協調傾向。observer_mode.md ダイヤル①に対応。
DOCTRINES: dict[str, dict[str, Any]] = {
    "cautious":     {"space_ratio": 0.50, "w": (0.40, 0.30, 0.30), "diplo": 0.15},
    "expansionist": {"space_ratio": 0.80, "w": (0.30, 0.55, 0.15), "diplo": 0.05},
    "cooperative":  {"space_ratio": 0.60, "w": (0.40, 0.35, 0.25), "diplo": 0.55},
    "commercial":   {"space_ratio": 0.70, "w": (0.30, 0.50, 0.20), "diplo": 0.20},
    "balanced":     {"space_ratio": 0.65, "w": (0.34, 0.33, 0.33), "diplo": 0.25},
}


class DecisionProvider:
    """基底。使うメソッドだけ override する。"""

    def national_decision(self, state: GameState, nation: Nation) -> NationalDecision:
        raise NotImplementedError

    def god_decision(self, state: GameState, book: EventBook) -> GodDecision | None:
        return None


class DoctrineProvider(DecisionProvider):
    """国家AI。ドクトリンに従って自国（または自律他国）の手を決める。

    観測モードではプレイヤー自国もこれに委任される（いつでもAI委任）。
    """

    def __init__(self, doctrine: str, rng: Rng):
        self.doctrine = doctrine if doctrine in DOCTRINES else "balanced"
        self.rng = rng

    def national_decision(self, state: GameState, nation: Nation) -> NationalDecision:
        d = DOCTRINES[self.doctrine]
        w = d["w"]
        # 地球が危機（世界世論が低い）局面では宇宙比率を自重する“内なる自律”
        ratio = d["space_ratio"]
        if state.kpi.public_will < 35:
            ratio *= 0.8
        # リスク負債が高ければ安全側に自己補正
        w_rd, w_mis, w_saf = w
        if nation.risk_debt > 55:
            w_saf += 0.15
            w_mis -= 0.15
        diplo = self.rng.chance(d["diplo"])
        return NationalDecision(ratio, w_rd, w_mis, w_saf, diplo).normalized()


class FateProvider(DecisionProvider):
    """運命AI（神レイヤーの自律）。observer_mode.md ダイヤル②。

    policy: passive(静観) / balanced(均衡) / harsh(苛烈) / random(無作為)
    """

    def __init__(self, policy: str, rng: Rng):
        self.policy = policy
        self.rng = rng

    def god_decision(self, state: GameState, book: EventBook) -> GodDecision | None:
        candidates = book.god_authorable(state)
        if not candidates:
            return None
        boons = [e for e in candidates if e.get("tone") == "boon"]
        crises = [e for e in candidates if e.get("tone") == "crisis"]
        m = state.kpi.mandate

        if self.policy == "passive":
            return None
        if self.policy == "random":
            if self.rng.chance(0.18):
                return GodDecision(self.rng.choice(candidates)["id"])
            return None
        if self.policy == "harsh":
            # ドラマ重視：Mandate が貯まれば積極的に危機を投げて世界を試す
            if crises and m >= 30 and self.rng.chance(0.40):
                return GodDecision(self.rng.choice(crises)["id"])
            return None
        if self.policy == "balanced":
            # 調停者の神：停滞時は好況で後押し、過熱時のみ引き締める
            stalling = state.kpi.spacefaring < (state.turn * 0.8)
            overheated = state.kpi.risk_debt > 65
            if stalling and boons and m >= 12 and self.rng.chance(0.35):
                return GodDecision(self.rng.choice(boons)["id"])
            if overheated and crises and m >= 20 and self.rng.chance(0.30):
                return GodDecision(self.rng.choice(crises)["id"])
        return None

    # 運命AIがイベントの“決着”を選ぶ（events.md の choice 選択）
    def choose_choice_index(self, event: dict[str, Any]) -> int:
        choices = event.get("choices", [])
        if not choices:
            return -1
        if self.policy == "harsh":
            return len(choices) - 1   # より波乱を呼ぶ選択に寄せる
        if self.policy == "random":
            return min(len(choices) - 1, int(self.rng.uniform(0, len(choices))))
        return 0                       # 穏当な既定選択


class ScriptedGodProvider(DecisionProvider):
    """テスト・デモ用：指定ターンに指定イベントを起動する決定論プロバイダ。"""

    def __init__(self, schedule: dict[int, str]):
        self.schedule = schedule  # turn -> event_id

    def god_decision(self, state: GameState, book: EventBook) -> GodDecision | None:
        ev = self.schedule.get(state.turn)
        if ev and book.get(ev) and book.get(ev).get("mandate_cost", 0) <= state.kpi.mandate:
            return GodDecision(ev)
        return None
