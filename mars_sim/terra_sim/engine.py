"""シミュレーションエンジン（M0）。

コアループ（game_design §5）：
  方針(配分) → 時間を進める → 物理と確率で帰結 → 人類指標が更新。

「誰が決めるか」は DecisionProvider に委ねる（providers.py）ので、
本エンジンは標準プレイ／観測モードを区別しない。
数値定数はチューニング用にここへ集約（将来は data/ へ外出し可）。
"""

from __future__ import annotations

from .events import EventBook, resolve_choice
from .loader import load_missions
from .providers import (
    DecisionProvider,
    DoctrineProvider,
    FateProvider,
    GodDecision,
    NationalDecision,
)
from .rng import Rng
from .state import MILESTONES, GameState, Mission, Nation, clamp

# マイルストン key → ミッション・テンプレート（leo_economy は能力で自動到達）
TEMPLATE_FOR = {
    "lunar_base": "moon_crewed",
    "lunar_isru": "moon_isru",
    "mars_landing": "mars_crewed",
    "mars_settlement": "mars_settle",
}
AUTO_MILESTONES = {"leo_economy"}


def _dest(m: Mission) -> str:
    return "火星" if m.dest == "mars" else "月"

# --- チューニング定数（基準値・実装で調整）---------------------------------
INVEST_SCALE = 0.11        # 投資→各能力への変換係数
RISK_FROM_MISSIONS = 0.16  # ミッション偏重が生むリスク負債
RISK_RELIEF_SAFETY = 0.14  # 安全投資によるリスク低減
ACCIDENT_BASE = 0.015      # 事故の基礎確率
ACCIDENT_PER_RISK = 0.006  # リスク負債1あたりの追加事故確率
MANDATE_REGEN = 1.0        # 毎ターンの天命回復
BUDGET_REVERT = 0.06       # 予算が基準値へ回帰する速さ（一度の不況で永続しない）
WINTER_WILL = 15.0         # この世界世論を割ると「宇宙の冬」


class Engine:
    """1ゲームの進行を司る。providers を差し替えれば遊び方が変わる。"""

    def __init__(
        self,
        state: GameState,
        event_book: EventBook,
        *,
        seed: int = 0,
        national_providers: dict[str, DecisionProvider] | None = None,
        god_provider: DecisionProvider | None = None,
    ):
        self.state = state
        self.book = event_book
        self.rng = Rng(seed)
        # M1: ミッション・テンプレートと火星窓の定義
        mdata = load_missions()
        self.templates: dict = mdata["templates"]
        self.window_cfg: dict = mdata["mars_window"]
        # 既定：他国は default_doctrine で自律、自国も（observer 用に）自律を仮置き
        self.national_providers = national_providers or {}
        for nid, n in state.nations.items():
            self.national_providers.setdefault(
                nid, DoctrineProvider(n.default_doctrine, self.rng)
            )
        # 神レイヤー既定：均衡の運命AI
        self.god_provider = god_provider or FateProvider("balanced", self.rng)

    # ---- 1ターン --------------------------------------------------------
    def run_turn(self) -> None:
        s = self.state
        s.turn += 1
        s.year += 1

        # ① 各国の意思決定と投資（自国＝指示 or 委任、他国＝自律）
        for n in s.nations.values():
            decision = self.national_providers[n.id].national_decision(s, n)
            self._apply_national(n, decision)

        # ② 事故判定（リスク負債が高いほど起こる）
        for n in s.nations.values():
            self._accident_check(n)

        # ③ マイルストン：LEO は能力で自動、それ以降はミッション・パイプライン（M1）
        for n in s.nations.values():
            self._auto_milestone_check(n)
            self._mission_phase(n)

        # ④ 神レイヤー：能動起動（Mandate 消費）or 自然進行
        self._god_phase()

        # ⑤ グローバル更新（集約・回復・ドリフト）
        self._global_update()

        # ⑥ 宇宙の冬の判定
        self._winter_check()

    def run(self, turns: int) -> None:
        for _ in range(turns):
            if self.state.space_winter:
                break
            self.run_turn()

    # ---- 各フェーズ -----------------------------------------------------
    def _apply_national(self, n: Nation, d: NationalDecision) -> None:
        invest = n.budget * d.space_ratio * INVEST_SCALE
        n.capability = clamp(n.capability + invest * d.w_rd, 0, 200)
        n.spacefaring = clamp(n.spacefaring + invest * d.w_missions * (0.6 + n.capability / 150), 0, 120)
        # 安全 vs 速度：ミッション偏重はリスク負債を積み、安全投資は返済する
        n.risk_debt = clamp(
            n.risk_debt
            + invest * d.w_missions * RISK_FROM_MISSIONS
            - invest * d.w_safety * RISK_RELIEF_SAFETY
            - 0.15,  # 時間経過で僅かに自然返済
        )
        # 宇宙比率が高すぎると国内支持が削れる（地球の他需要との競合）
        will_drift = 0.5 - (d.space_ratio - 0.6) * 6
        n.public_will = clamp(n.public_will + will_drift)
        # 協調アクション：世界協調度と他国の信頼を育てる
        if d.diplomacy:
            self.state.kpi.cohesion = clamp(self.state.kpi.cohesion + 1.5 * (n.cohesion_affinity / 60))
            if n.is_player:
                for o in self.state.others:
                    o.trust_to_player = clamp(o.trust_to_player + 1.0, 0, 200)

    def _accident_check(self, n: Nation) -> None:
        p = ACCIDENT_BASE + ACCIDENT_PER_RISK * n.risk_debt
        if not self.rng.chance(min(0.6, p)):
            return
        s = self.state
        n.spacefaring = clamp(n.spacefaring - self.rng.uniform(2, 6), 0, 120)
        n.risk_debt = clamp(n.risk_debt - 8)  # 教訓として一部清算
        n.public_will = clamp(n.public_will - 8)
        s.kpi.public_will = clamp(s.kpi.public_will - 3)
        # 有人段階以降は人的損失もありうる
        if "mars_landing" in n.reached or "lunar_base" in n.reached:
            if s.kpi.offworld_pop > 0 and self.rng.chance(0.5):
                lost = min(s.kpi.offworld_pop, self.rng.choice([1, 1, 2, 3]))
                s.kpi.offworld_pop -= lost
                s.log(f"{n.name_ja}の事故で{lost}名の宇宙飛行士が犠牲に。世界が沈黙した。", "accident")
                return
        s.log(f"{n.name_ja}でミッション事故。計画が後退した。", "accident")

    def _auto_milestone_check(self, n: Nation) -> None:
        """能力で自動到達するマイルストン（LEO経済）。"""
        for threshold, key, label in MILESTONES:
            if key in AUTO_MILESTONES and n.spacefaring >= threshold and key not in n.reached:
                n.reached.add(key)
                self._on_milestone(n, key, label)

    # ---- M1: ミッション・パイプライン -----------------------------------
    def mars_window(self, year: int) -> bool:
        """その年が火星打ち上げ窓か（会合周期 約2.135年）。"""
        period = self.window_cfg["synodic_years"]
        base = self.window_cfg["reference_year"]
        tol = self.window_cfg["tolerance_years"]
        k = round((year - base) / period)
        return abs((base + k * period) - year) < tol

    def _next_target(self, n: Nation) -> tuple[str, str, str] | None:
        """次に挑むべきマイルストン（key, label, template_id）。順序どおり最初の未達。"""
        for _threshold, key, label in MILESTONES:
            if key in n.reached:
                continue
            if key in AUTO_MILESTONES:
                return None  # LEO 未達のうちはミッション着手しない
            return key, label, TEMPLATE_FOR[key]
        return None

    def _mission_phase(self, n: Nation) -> None:
        if self.state.space_winter:
            return
        if n.mission is None:
            self._maybe_launch(n)
        else:
            self._advance_mission(n)

    def _maybe_launch(self, n: Nation) -> None:
        target = self._next_target(n)
        if target is None:
            return
        key, _label, template_id = target
        tmpl = self.templates[template_id]
        provider = self.national_providers[n.id]
        buffer = provider.launch_buffer()
        if n.spacefaring < tmpl["requires_capacity"] + buffer:
            return
        if not provider.confirm_launch(self.state, n, template_id, tmpl):
            return
        n.mission = Mission(
            template=template_id, dest=tmpl["dest"], kind=tmpl["kind"],
            grants=tmpl["grants"], stage="build",
            remaining=int(tmpl["build_turns"]), needs_window=bool(tmpl["needs_window"]),
        )
        dest_ja = {"moon": "月", "mars": "火星"}.get(tmpl["dest"], tmpl["dest"])
        kind_ja = {"crewed": "有人", "cargo": "無人補給"}.get(tmpl["kind"], tmpl["kind"])
        self.state.log(f"{n.name_ja}が{dest_ja}{kind_ja}ミッションの建造を開始。", "mission")

    def _fail_prob(self, n: Nation, base: float) -> float:
        """段階失敗確率：技術成熟度が下げ、リスク負債が上げる。"""
        p = base * (1 + n.risk_debt * 0.015) * (1 - (n.capability - 80) * 0.005)
        return max(0.01, min(0.9, p))

    def _advance_mission(self, n: Nation) -> None:
        m = n.mission
        assert m is not None
        tmpl = self.templates[m.template]
        s = self.state

        if m.stage == "build":
            m.remaining -= 1
            if m.remaining <= 0:
                # 建造完了 → 打ち上げ（上昇段の失敗判定）
                if self.rng.chance(self._fail_prob(n, tmpl["failure"]["ascent"])):
                    self._mission_failed(n, "ascent")
                    return
                m.stage = "await_window" if m.needs_window else "transit"
                m.remaining = int(tmpl["transit_turns"])
                if m.stage == "transit":
                    s.log(f"{n.name_ja}：打ち上げ成功、{_dest(m)}へ遷移中。", "mission")
            return

        if m.stage == "await_window":
            if self.mars_window(s.year):
                m.stage = "transit"
                m.remaining = int(tmpl["transit_turns"])
                s.log(f"{n.name_ja}：火星打ち上げ窓が開く——{_dest(m)}へ向け出発。", "mission")
            return

        if m.stage == "transit":
            if m.remaining > 0:
                m.remaining -= 1
                return
            # 遷移完了 → 有人は航行リスク（生命維持・放射線）
            if m.kind == "crewed" and self.rng.chance(self._fail_prob(n, tmpl["failure"]["transit"])):
                self._mission_failed(n, "transit")
                return
            m.stage = "edl"
            return

        if m.stage == "edl":
            # EDL（突入・降下・着陸）— 火星最大の難所
            if self.rng.chance(self._fail_prob(n, tmpl["failure"]["edl"])):
                self._mission_failed(n, "edl")
                return
            self._mission_succeeded(n)

    def _mission_succeeded(self, n: Nation) -> None:
        m = n.mission
        assert m is not None
        for _t, key, label in MILESTONES:
            if key == m.grants:
                n.reached.add(key)
                self._on_milestone(n, key, label)
                break
        n.mission = None

    def _mission_failed(self, n: Nation, stage: str) -> None:
        m = n.mission
        assert m is not None
        s = self.state
        n.risk_debt = clamp(n.risk_debt + 6)
        n.spacefaring = clamp(n.spacefaring - self.rng.uniform(1, 4), 0, 120)
        stage_ja = {"ascent": "打ち上げ", "transit": "航行中", "edl": "着陸(EDL)"}.get(stage, stage)
        if m.kind == "crewed":
            n.public_will = clamp(n.public_will - 12)
            s.kpi.public_will = clamp(s.kpi.public_will - 6)
            dest_ja = "火星" if m.dest == "mars" else "月"
            s.log(f"{n.name_ja}の{dest_ja}有人ミッションが{stage_ja}で失敗——乗員が失われた。世界が沈黙する。", "accident")
        else:
            n.public_will = clamp(n.public_will - 3)
            s.log(f"{n.name_ja}の無人ミッションが{stage_ja}で失敗。再挑戦へ。", "accident")
        n.mission = None

    def _on_milestone(self, n: Nation, key: str, label: str) -> None:
        s = self.state
        first = not any(key in o.reached for o in s.nations.values() if o is not n)
        # 人類初の偉業は世界世論と天命を大きく押し上げる
        will = 8 if first else 3
        mandate = {"lunar_isru": 15, "mars_landing": 20, "mars_settlement": 18}.get(key, 6)
        s.kpi.public_will = clamp(s.kpi.public_will + will)
        s.kpi.mandate = clamp(s.kpi.mandate + (mandate if first else mandate * 0.3))
        s.kpi.knowledge = clamp(s.kpi.knowledge + 4)
        if first:
            n.trust_to_player = clamp(n.trust_to_player + 5, 0, 200)
        # 居住の進展で地球外人口が増える
        if key == "lunar_base":
            s.kpi.offworld_pop += 4
        elif key == "mars_landing":
            s.kpi.offworld_pop += 6
        elif key == "mars_settlement":
            s.kpi.offworld_pop += 20
            s.kpi.self_sufficiency = clamp(s.kpi.self_sufficiency + 15)
        tag = "人類初・" if first else ""
        s.log(f"{tag}{n.name_ja}が「{label}」を達成。", "milestone")

    def _god_phase(self) -> None:
        s = self.state
        decision = self.god_provider.god_decision(s, self.book)
        if not isinstance(decision, GodDecision):
            return
        event = self.book.get(decision.event_id)
        if event is None:
            return
        cost = event.get("mandate_cost", 0)
        if cost > s.kpi.mandate:
            return
        s.kpi.mandate = clamp(s.kpi.mandate - cost)
        # 決着（choice）の選択：神プロバイダが選ぶ／無ければ既定0
        idx = 0
        if hasattr(self.god_provider, "choose_choice_index"):
            idx = self.god_provider.choose_choice_index(event)
        choices = event.get("choices", [])
        if not choices:
            return
        idx = max(0, min(idx, len(choices) - 1))
        choice = choices[idx]
        notes = resolve_choice(s, choice, self.rng)
        detail = f"（{notes[0]}）" if notes else ""
        s.log(f"神の介入：{event.get('id')} → {choice.get('label', '')}{detail}", "god")

    def _global_update(self) -> None:
        s = self.state
        # 予算は基準値へ緩やかに回帰（イベントの増減は一時的ショック）
        for n in s.nations.values():
            n.budget = clamp(n.budget + (n.budget_base - n.budget) * BUDGET_REVERT, 0, 200)
        # 宇宙進出力は全勢力の集約
        s.kpi.spacefaring = clamp(sum(n.spacefaring for n in s.nations.values()) / len(s.nations))
        s.kpi.risk_debt = clamp(sum(n.risk_debt for n in s.nations.values()) / len(s.nations))
        s.kpi.knowledge = clamp(s.kpi.knowledge + sum(n.capability for n in s.nations.values()) / len(s.nations) * 0.01)
        # 世界世論は穏やかに基準回帰、天命は時間で回復（協調が高いほど速い）
        s.kpi.public_will = clamp(s.kpi.public_will + (50 - s.kpi.public_will) * 0.03)
        s.kpi.mandate = clamp(s.kpi.mandate + MANDATE_REGEN + s.kpi.cohesion * 0.01)
        # 火星定住地があれば、知識と協調に応じて自立度がゆっくり育つ
        if s.kpi.offworld_pop > 0 and any("mars_landing" in n.reached for n in s.nations.values()):
            s.kpi.self_sufficiency = clamp(s.kpi.self_sufficiency + 0.3 + s.kpi.knowledge * 0.01)

    def _winter_check(self) -> None:
        s = self.state
        if not s.space_winter and s.kpi.public_will < WINTER_WILL:
            s.space_winter = True
            for n in s.nations.values():
                n.budget = clamp(n.budget * 0.6, 0, 200)
            s.log("世界が宇宙から目を背けた——『宇宙の冬』が訪れる。", "god")


def make_observer_engine(
    player_id: str,
    *,
    seed: int = 0,
    self_doctrine: str | None = None,
    fate_policy: str = "balanced",
    start_year: int = 2035,
) -> Engine:
    """観測モード用エンジンを組み立てるヘルパ。

    自国も DoctrineProvider に委任し、神は FateProvider に委ねる
    （= 完全自律進行）。self_doctrine 未指定なら自国の既定ドクトリン。
    """
    from .loader import new_game

    state = new_game(player_id, start_year=start_year)
    rng = Rng(seed)
    nat: dict[str, DecisionProvider] = {}
    for nid, n in state.nations.items():
        doc = self_doctrine if (n.is_player and self_doctrine) else n.default_doctrine
        nat[nid] = DoctrineProvider(doc, rng)
    god = FateProvider(fate_policy, rng)
    return Engine(state, EventBook_from_data(), seed=seed,
                  national_providers=nat, god_provider=god)


def EventBook_from_data() -> EventBook:
    from .loader import load_events
    return EventBook(load_events())
