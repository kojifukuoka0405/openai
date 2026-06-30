"""M0 コアの単体テスト。

決定論・データ読み込み・イベント効果・マイルストン・DecisionProvider抽象を検証。
実行: mars_sim/ をカレントにして `python -m pytest tests` または `python tests/test_core.py`
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from terra_sim.engine import Engine, EventBook_from_data, make_observer_engine
from terra_sim.events import EventBook, apply_effects
from terra_sim.loader import load_events, load_nations, new_game
from terra_sim.providers import DoctrineProvider, FateProvider, ScriptedGodProvider
from terra_sim.rng import Rng


# ---- データ読み込み -------------------------------------------------------
def test_three_playable_nations():
    nations = load_nations()
    assert set(nations) == {"japan", "usa", "china"}


def test_events_loaded():
    ids = {e["id"] for e in load_events()}
    assert {"clim_megadrought", "econ_boom", "econ_bust"} <= ids


def test_new_game_player_flag():
    state = new_game("japan")
    assert state.player.id == "japan"
    assert state.player.is_player
    assert all(not n.is_player for n in state.others)
    assert len(state.others) == 2


# ---- イベント効果 ---------------------------------------------------------
def test_apply_effects_budget_and_kpi_clamp():
    state = new_game("usa")
    state.kpi.public_will = 5.0
    apply_effects(state, {"factions_all": {"budget_pct": -50}, "kpi": {"public_will": -100}})
    # budget は割合で減る、KPI は 0 でクランプ
    assert state.nations["usa"].budget < 50
    assert state.kpi.public_will == 0.0


def test_god_event_deducts_mandate():
    state = new_game("japan")
    book = EventBook(load_events())
    start = state.kpi.mandate
    engine = Engine(state, book, seed=1,
                    god_provider=ScriptedGodProvider({1: "econ_bust"}))
    engine.run_turn()
    # econ_bust のコスト 8 が引かれる（毎ターンの自然回復ぶんは戻る）
    assert state.kpi.mandate <= start - 8 + 5


def test_god_cannot_author_without_mandate():
    state = new_game("japan")
    state.kpi.mandate = 2.0  # コスト未満
    book = EventBook(load_events())
    engine = Engine(state, book, seed=1,
                    god_provider=ScriptedGodProvider({1: "econ_bust"}))
    engine.run_turn()
    # 起動されなかった（年代記に神の介入が無い）
    assert not any(e.kind == "god" for e in state.chronicle)


# ---- 進行とマイルストン ---------------------------------------------------
def test_observer_progresses_spacefaring():
    engine = make_observer_engine("usa", seed=5, fate_policy="balanced")
    before = engine.state.kpi.spacefaring
    engine.run(40)
    assert engine.state.turn == 40
    assert engine.state.kpi.spacefaring > before


def test_milestone_grants_mandate_and_logs():
    state = new_game("usa")
    book = EventBook([])  # 神イベント無しで純粋に進行
    engine = Engine(state, book, seed=2, god_provider=FateProvider("passive", Rng(2)))
    engine.run(40)
    reached = set().union(*(n.reached for n in state.nations.values()))
    assert "leo_economy" in reached
    assert any(e.kind == "milestone" for e in state.chronicle)


# ---- 決定論（リプレイ可能性）---------------------------------------------
def test_determinism_same_seed_same_history():
    a = make_observer_engine("japan", seed=42, fate_policy="harsh")
    b = make_observer_engine("japan", seed=42, fate_policy="harsh")
    a.run(40)
    b.run(40)
    assert a.state.snapshot() == b.state.snapshot()
    assert [e.line() for e in a.state.chronicle] == [e.line() for e in b.state.chronicle]


def test_different_seed_diverges():
    a = make_observer_engine("japan", seed=1, fate_policy="harsh")
    b = make_observer_engine("japan", seed=2, fate_policy="harsh")
    a.run(40)
    b.run(40)
    assert a.state.snapshot() != b.state.snapshot()


# ---- DecisionProvider 抽象：同一エンジンで委任先を差し替えられる ----------
def test_provider_swap_same_engine():
    # 自国を「慎重」に委任した場合と「拡張」に委任した場合で歴史が変わる
    cautious = make_observer_engine("usa", seed=9, self_doctrine="cautious", fate_policy="passive")
    aggressive = make_observer_engine("usa", seed=9, self_doctrine="expansionist", fate_policy="passive")
    cautious.run(40)
    aggressive.run(40)
    # 拡張のほうが自国の進出が速い（or リスク負債が高い）傾向
    cu = cautious.state.nations["usa"]
    ag = aggressive.state.nations["usa"]
    assert (ag.spacefaring, ag.risk_debt) != (cu.spacefaring, cu.risk_debt)


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
