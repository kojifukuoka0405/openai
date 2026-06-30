"""決定論的な乱数。リプレイ可能性のため、必ずシード管理する。

game_design §10「決定論とリプレイ」に対応。エンジン内の確率判定は
すべてこの Rng を経由し、同一シード＋同一入力なら同一の歴史を再生する。
"""

from __future__ import annotations

import random


class Rng:
    """シード固定の薄いラッパ。標準 random をそのまま使うより、
    どの確率判定もここを通す規律を強制するために包んでいる。"""

    def __init__(self, seed: int = 0):
        self.seed = seed
        self._r = random.Random(seed)

    def chance(self, p: float) -> bool:
        """確率 p（0..1）で True。"""
        return self._r.random() < p

    def uniform(self, a: float, b: float) -> float:
        return self._r.uniform(a, b)

    def jitter(self, scale: float = 1.0) -> float:
        """-scale..+scale の揺らぎ。決定論的なノイズ用。"""
        return self._r.uniform(-scale, scale)

    def choice(self, seq):
        return self._r.choice(seq)
