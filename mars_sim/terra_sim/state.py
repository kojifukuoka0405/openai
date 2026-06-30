"""ゲーム状態モデル。

「数値は data に、ルールは core に」（game_design §10）。本ファイルは
状態の“器”だけを定義し、進行ルールは engine.py が持つ。
KPI 名・勢力効果の語彙は game_design §6.2 / events.md と一致させる。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


# 火星定住地の自立を測る到達点（spacefaring スコアの閾値）
MILESTONES = [
    (20, "leo_economy", "LEO経済の確立"),
    (40, "lunar_base", "月面拠点の建設"),
    (55, "lunar_isru", "月面ISRUの実証"),
    (72, "mars_landing", "有人火星到達"),
    (88, "mars_settlement", "火星定住地の自立"),
]


@dataclass
class Nation:
    """国家（自国＝プレイヤーの分身、他国＝自律勢力）。

    params は data/nations/nations.json 由来の初期値（0-100、budget は相対指数）。
    """

    id: str
    name_ja: str
    is_player: bool = False
    default_doctrine: str = "balanced"

    # 動的ステータス（初期値は loader が params から流し込む）
    budget: float = 50.0          # 予算規模（相対指数、イベントで増減）
    budget_base: float = 50.0     # 予算の基準値（イベント後はここへ緩やかに回帰）
    capability: float = 50.0      # 技術成熟度
    spacefaring: float = 0.0      # 蓄積した宇宙進出インフラ／到達度
    risk_debt: float = 0.0        # リスク負債（高いほど事故確率↑）
    public_will: float = 60.0     # 国内の宇宙支持
    cohesion_affinity: float = 50.0  # 協調親和（共同事業での倍化しやすさ）
    commercial: float = 50.0      # 民間打ち上げ層（$/kg の安さ）
    trust_to_player: float = 50.0  # 自国への信頼（他国のみ意味を持つ）

    reached: set[str] = field(default_factory=set)  # 達成済みマイルストン

    def snapshot(self) -> dict[str, Any]:
        d = asdict(self)
        d["reached"] = sorted(self.reached)
        return d


@dataclass
class GlobalKPIs:
    """人類指標（グローバル）。game_design §6.2。"""

    cohesion: float = 50.0          # 地球協調度
    public_will: float = 55.0       # 宇宙への世界世論
    knowledge: float = 10.0         # 科学知（技術ツリー進捗）
    risk_debt: float = 0.0          # 世界全体のリスク負債
    mandate: float = 50.0           # 神レイヤーの基軸資源（天命）
    spacefaring: float = 0.0        # 宇宙進出力（全勢力の集約）
    offworld_pop: int = 0           # 地球外人口
    self_sufficiency: float = 0.0   # 火星定住地の自立度

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChronicleEntry:
    """年代記の1行。観測モードで流れるドキュメンタリ調の記録。"""

    year: int
    text: str
    kind: str = "info"  # info | milestone | accident | god | diplomacy

    def line(self) -> str:
        mark = {
            "milestone": "★",
            "accident": "✖",
            "god": "⚡",
            "diplomacy": "🤝",
        }.get(self.kind, "・")
        return f"{self.year}年 {mark} {self.text}"


@dataclass
class GameState:
    year: int
    turn: int
    kpi: GlobalKPIs
    nations: dict[str, Nation]
    player_id: str
    flags: dict[str, Any] = field(default_factory=dict)
    chronicle: list[ChronicleEntry] = field(default_factory=list)
    space_winter: bool = False

    @property
    def player(self) -> Nation:
        return self.nations[self.player_id]

    @property
    def others(self) -> list[Nation]:
        return [n for n in self.nations.values() if not n.is_player]

    def log(self, text: str, kind: str = "info") -> None:
        self.chronicle.append(ChronicleEntry(self.year, text, kind))

    def snapshot(self) -> dict[str, Any]:
        """決定論検証・リプレイ用の状態ダイジェスト。"""
        return {
            "year": self.year,
            "turn": self.turn,
            "kpi": self.kpi.snapshot(),
            "nations": {k: v.snapshot() for k, v in self.nations.items()},
            "flags": dict(self.flags),
            "space_winter": self.space_winter,
        }
