"""CLI エントリ。観測モード（自律進行の鑑賞）と最小のキャンペーン操作。

  python -m terra_sim.cli                      # 観測モードのデモ（日本・均衡の運命）
  python -m terra_sim.cli --mode observer --nation usa --fate harsh --turns 40
  python -m terra_sim.cli --mode campaign --nation japan --turns 12
"""

from __future__ import annotations

import argparse

from .engine import Engine, EventBook_from_data
from .loader import load_nations, new_game
from .providers import (
    DOCTRINES,
    DecisionProvider,
    DoctrineProvider,
    FateProvider,
    GodDecision,
    NationalDecision,
)
from .rng import Rng
from .state import MILESTONES, GameState


# ---- 表示ヘルパ ----------------------------------------------------------
def print_dashboard(state: GameState) -> None:
    k = state.kpi
    print(f"\n── {state.year}年（ターン{state.turn}）"
          + ("  ❄ 宇宙の冬" if state.space_winter else ""))
    print(f"   協調 {k.cohesion:5.1f} | 世論 {k.public_will:5.1f} | 知識 {k.knowledge:5.1f} "
          f"| リスク {k.risk_debt:5.1f} | 天命 {k.mandate:5.1f}")
    print(f"   宇宙進出力 {k.spacefaring:5.1f} | 地球外人口 {k.offworld_pop:3d} "
          f"| 火星自立度 {k.self_sufficiency:5.1f}")
    for n in state.nations.values():
        tag = "▶自国" if n.is_player else "  他国"
        ms = "".join("●" if key in n.reached else "○" for _, key, _ in MILESTONES)
        print(f"   {tag} {n.name_ja:4s} 予算{n.budget:5.1f} 技術{n.capability:5.1f} "
              f"進出{n.spacefaring:5.1f} リスク{n.risk_debt:4.1f}  到達[{ms}]")


def print_chronicle(state: GameState, last: int | None = None) -> None:
    entries = state.chronicle if last is None else state.chronicle[-last:]
    if not entries:
        return
    print("\n── 年代記 ──")
    for e in entries:
        print("   " + e.line())


# ---- 最小のキャンペーン用 Human プロバイダ -------------------------------
class CliHumanProvider(DecisionProvider):
    """標準プレイ用。自国の方針と神レイヤーの起動を端末から尋ねる（最小実装）。"""

    def __init__(self, rng: Rng):
        self.rng = rng

    def national_decision(self, state: GameState, nation: NationalDecision) -> NationalDecision:  # type: ignore[override]
        print(f"\n[{state.year}年] {nation.name_ja}の方針を選択：")
        print("  1) 慎重（安全重視・着実）  2) 均衡  3) 拡張（攻撃的・高リスク）  4) 協調（共同事業）")
        choice = _ask_int("選択 (1-4, 既定2): ", default=2, lo=1, hi=4)
        doctrine = {1: "cautious", 2: "balanced", 3: "expansionist", 4: "cooperative"}[choice]
        return DoctrineProvider(doctrine, self.rng).national_decision(state, nation)

    def god_decision(self, state: GameState, book) -> GodDecision | None:
        authorable = book.god_authorable(state)
        if not authorable:
            return None
        print(f"\n[神レイヤー] 天命(Mandate)={state.kpi.mandate:.0f}。地球規模イベントを起こす？")
        print("  0) 何もしない（天命を温存）")
        for i, e in enumerate(authorable, 1):
            print(f"  {i}) {e['id']}（コスト{e.get('mandate_cost',0)}） — {e.get('narrative','')[:32]}…")
        sel = _ask_int("選択 (既定0): ", default=0, lo=0, hi=len(authorable))
        if sel == 0:
            return None
        return GodDecision(authorable[sel - 1]["id"])

    # 神が起こしたイベントの決着は最初の選択肢（最小実装）
    def choose_choice_index(self, event) -> int:
        return 0


def _ask_int(prompt: str, *, default: int, lo: int, hi: int) -> int:
    try:
        raw = input(prompt).strip()
    except EOFError:
        return default
    if not raw:
        return default
    try:
        v = int(raw)
        return v if lo <= v <= hi else default
    except ValueError:
        return default


# ---- モード実行 ----------------------------------------------------------
def run_observer(args) -> None:
    state = new_game(args.nation, start_year=args.start_year)
    rng = Rng(args.seed)
    providers: dict[str, DecisionProvider] = {}
    for nid, n in state.nations.items():
        doc = args.doctrine if (n.is_player and args.doctrine) else n.default_doctrine
        providers[nid] = DoctrineProvider(doc, rng)
    god = FateProvider(args.fate, rng)
    engine = Engine(state, EventBook_from_data(), seed=args.seed,
                    national_providers=providers, god_provider=god)

    print(f"観測モード：自国={state.player.name_ja} / 自国AI={providers[args.nation].doctrine} "
          f"/ 運命={args.fate} / seed={args.seed}")
    print("（完全自律進行を鑑賞します。介入したい時は --mode campaign を）")
    engine.run(args.turns)
    print_chronicle(state)
    print_dashboard(state)
    _print_outcome(state)


def run_campaign(args) -> None:
    state = new_game(args.nation, start_year=args.start_year)
    rng = Rng(args.seed)
    human = CliHumanProvider(rng)
    # 自国は人間、他国は自律、神も人間（標準プレイ）
    providers: dict[str, DecisionProvider] = {args.nation: human}
    engine = Engine(state, EventBook_from_data(), seed=args.seed,
                    national_providers=providers, god_provider=human)

    print(f"キャンペーン：自国={state.player.name_ja} / seed={args.seed}")
    for _ in range(args.turns):
        if state.space_winter:
            break
        engine.run_turn()
        print_chronicle(state, last=4)
        print_dashboard(state)
    _print_outcome(state)


def _print_outcome(state: GameState) -> None:
    print("\n══ 結果 ══")
    if state.space_winter:
        print("  人類は揺りかごに引きこもった（宇宙の冬）。")
    elif any("mars_settlement" in n.reached for n in state.nations.values()):
        print("  人類は火星に根を下ろした——多惑星種への第一歩。")
    elif any("mars_landing" in n.reached for n in state.nations.values()):
        print("  赤い惑星に最初の足跡。だが定住への道はまだ遠い。")
    else:
        leader = max(state.nations.values(), key=lambda n: n.spacefaring)
        print(f"  火星到達は未達。先頭は{leader.name_ja}（進出力{leader.spacefaring:.0f}）。")


def build_parser() -> argparse.ArgumentParser:
    nations = list(load_nations().keys())
    p = argparse.ArgumentParser(description="TERRA ASCENDANT — 火星探査シミュレーション（M0）")
    p.add_argument("--mode", choices=["observer", "campaign"], default="observer")
    p.add_argument("--nation", choices=nations, default="japan", help="自国")
    p.add_argument("--turns", type=int, default=30)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--start-year", dest="start_year", type=int, default=2035)
    p.add_argument("--fate", choices=["passive", "balanced", "harsh", "random"],
                   default="balanced", help="観測モードの運命AIの方針")
    p.add_argument("--doctrine", choices=list(DOCTRINES.keys()), default=None,
                   help="観測モードで自国に委任するドクトリン（既定は国別）")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.mode == "observer":
        run_observer(args)
    else:
        run_campaign(args)


if __name__ == "__main__":
    main()
