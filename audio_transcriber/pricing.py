"""モデルの一覧と、最新の料金の取得。

アプリ起動のたびに公開されている価格表を取得し、**そのときの実勢価格**を
画面に出す。取得できないとき（オフラインなど）は、直近のキャッシュ →
コードに埋め込んだ参考価格 の順にフォールバックする。

価格表の取得元は LiteLLM が公開しているモデル価格 JSON。OpenAI は機械可読な
価格 API を提供していないため、広く使われている公開データを参照している。

用語:
- 文字起こしの料金は「音声 1 分あたり」に換算して表示する
- 推敲の料金は「入力/出力 100 万トークンあたり」。実際の見積りは
  録音時間から文字数を推定して算出する
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .config import CONFIG_DIR

PRICE_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
PRICE_SOURCE_NAME = "LiteLLM 公開価格表"
CACHE_PATH = CONFIG_DIR / "prices.json"
FETCH_TIMEOUT = 8.0

# 見積りに使う換算係数（おおよその値）
AUDIO_TOKENS_PER_MINUTE = 2400   # 音声 1 分あたりの音声トークン数
CHARS_PER_MINUTE = 300           # 日本語の話し言葉の 1 分あたり文字数
TOKENS_PER_CHAR = 0.8            # 日本語 1 文字あたりのトークン数
POLISH_OUTPUT_FACTOR = 1.6       # 推敲の出力トークン（推論分を含む）の係数
POLISH_INPUT_FACTOR = 1.3        # 指示文・文脈のぶんの上乗せ


@dataclass(frozen=True)
class ModelInfo:
    """選択肢として出すモデル。"""

    name: str
    kind: str                     # "transcribe" または "polish"
    note: str                     # 画面に出す短い説明


# 文字起こしの候補（この中から実際の価格で安い順に選抜する）
TRANSCRIBE_MODELS = [
    ModelInfo("gpt-transcribe", "transcribe", "最高精度・推奨"),
    ModelInfo("gpt-4o-mini-transcribe", "transcribe", "軽量・低コスト"),
    ModelInfo("gpt-4o-transcribe", "transcribe", "高精度"),
    ModelInfo("whisper-1", "transcribe", "従来モデル・実績豊富"),
    ModelInfo("gpt-4o-transcribe-diarize", "transcribe", "話者分離あり"),
]

# 推敲の候補
POLISH_MODELS = [
    ModelInfo("gpt-5.6-sol", "polish", "最高品質"),
    ModelInfo("gpt-5.6-terra", "polish", "品質とコストのバランス"),
    ModelInfo("gpt-5.6-luna", "polish", "最新世代で低コスト"),
    ModelInfo("gpt-5.4-nano", "polish", "低コスト"),
    ModelInfo("gpt-5-nano", "polish", "最安クラス"),
    ModelInfo("gpt-4.1-nano", "polish", "最安クラス"),
    ModelInfo("gpt-4.1-mini", "polish", "低コスト"),
    ModelInfo("gpt-4o-mini", "polish", "低コスト"),
]

ALL_MODELS = TRANSCRIBE_MODELS + POLISH_MODELS

# 取得に失敗したときに使う参考価格（2026-08-21 時点）。
# 単位は 1 トークンあたりの USD（音声は 1 秒あたり）。
BUILTIN_AS_OF = "2026-08-21"
BUILTIN_PRICES: Dict[str, dict] = {
    "gpt-transcribe": {"input_cost_per_second": 7.5e-05},
    "gpt-4o-transcribe": {"input_cost_per_audio_token": 2.5e-06,
                          "output_cost_per_token": 1e-05},
    "gpt-4o-mini-transcribe": {"input_cost_per_audio_token": 1.25e-06,
                               "output_cost_per_token": 5e-06},
    "gpt-4o-transcribe-diarize": {"input_cost_per_audio_token": 2.5e-06,
                                  "output_cost_per_token": 1e-05},
    "whisper-1": {"input_cost_per_second": 0.0001},
    "gpt-5.6-sol": {"input_cost_per_token": 5e-06, "output_cost_per_token": 3e-05},
    "gpt-5.6-terra": {"input_cost_per_token": 2e-06, "output_cost_per_token": 1.2e-05},
    "gpt-5.6-luna": {"input_cost_per_token": 2e-07, "output_cost_per_token": 1.2e-06},
    "gpt-5.4-nano": {"input_cost_per_token": 2e-07, "output_cost_per_token": 1.25e-06},
    "gpt-5-nano": {"input_cost_per_token": 5e-08, "output_cost_per_token": 4e-07},
    "gpt-4.1-nano": {"input_cost_per_token": 1e-07, "output_cost_per_token": 4e-07},
    "gpt-4.1-mini": {"input_cost_per_token": 4e-07, "output_cost_per_token": 1.6e-06},
    "gpt-4o-mini": {"input_cost_per_token": 1.5e-07, "output_cost_per_token": 6e-07},
}


@dataclass
class PriceTable:
    """モデル名 → 価格情報。どこから取れた値かも保持する。"""

    prices: Dict[str, dict] = field(default_factory=dict)
    origin: str = "builtin"          # live / cache / builtin
    fetched_at: Optional[float] = None   # UNIX 時刻
    error: Optional[str] = None

    # ------------------------------------------------------------ 表示
    def origin_label(self) -> str:
        """画面に出す「いつ・どこの価格か」。"""
        if self.origin == "live":
            return f"最新価格を取得しました（{self._stamp()} / {PRICE_SOURCE_NAME}）"
        if self.origin == "cache":
            return f"前回取得した価格を表示中（{self._stamp()}）"
        reason = f"：{self.error}" if self.error else ""
        return f"参考価格を表示中（{BUILTIN_AS_OF} 時点・取得できませんでした{reason}）"

    def _stamp(self) -> str:
        if not self.fetched_at:
            return "時刻不明"
        return datetime.fromtimestamp(self.fetched_at, timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )

    def is_live(self) -> bool:
        return self.origin == "live"

    # ------------------------------------------------------------ 計算
    def entry(self, model: str) -> dict:
        return self.prices.get(model) or BUILTIN_PRICES.get(model) or {}

    def transcribe_cost_per_minute(self, model: str) -> Optional[float]:
        """音声 1 分あたりの料金（USD）。分からなければ None。"""
        e = self.entry(model)
        per_second = e.get("input_cost_per_second")
        if per_second:
            return per_second * 60
        per_audio_token = e.get("input_cost_per_audio_token") or e.get("input_cost_per_token")
        if per_audio_token:
            return per_audio_token * AUDIO_TOKENS_PER_MINUTE
        return None

    def polish_cost_per_mtok(self, model: str) -> Optional[tuple]:
        """(入力, 出力) の 100 万トークンあたり料金（USD）。"""
        e = self.entry(model)
        cin = e.get("input_cost_per_token")
        cout = e.get("output_cost_per_token")
        if cin is None and cout is None:
            return None
        return ((cin or 0.0) * 1e6, (cout or 0.0) * 1e6)

    def estimate_transcribe(self, model: str, minutes: Optional[float]) -> Optional[float]:
        """録音時間から文字起こし料金を見積もる（USD）。"""
        rate = self.transcribe_cost_per_minute(model)
        if rate is None or minutes is None:
            return None
        return rate * minutes

    def estimate_polish(self, model: str, chars: Optional[float] = None,
                        minutes: Optional[float] = None) -> Optional[float]:
        """文字数（または録音時間）から推敲料金を見積もる（USD）。"""
        rates = self.polish_cost_per_mtok(model)
        if rates is None:
            return None
        if chars is None:
            if minutes is None:
                return None
            chars = minutes * CHARS_PER_MINUTE
        tokens = chars * TOKENS_PER_CHAR
        cin, cout = rates
        return (tokens * POLISH_INPUT_FACTOR * cin
                + tokens * POLISH_OUTPUT_FACTOR * cout) / 1e6


# --------------------------------------------------------------------------
# 取得
# --------------------------------------------------------------------------

def _relevant(raw: dict) -> Dict[str, dict]:
    """必要なモデルの価格だけ抜き出す（キャッシュを小さく保つ）。"""
    wanted = {m.name for m in ALL_MODELS}
    return {k: v for k, v in raw.items() if k in wanted and isinstance(v, dict)}


def fetch_prices(timeout: float = FETCH_TIMEOUT, url: str = PRICE_URL) -> Dict[str, dict]:
    """公開価格表をダウンロードして、必要な分だけ返す。"""
    request = urllib.request.Request(url, headers={"User-Agent": "audio-transcriber"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = json.loads(response.read().decode("utf-8"))
    prices = _relevant(raw)
    if not prices:
        raise ValueError("価格表に対象モデルが見つかりませんでした")
    return prices


def _read_cache() -> Optional[PriceTable]:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        prices = data.get("prices") or {}
        if not prices:
            return None
        return PriceTable(prices=prices, origin="cache", fetched_at=data.get("fetched_at"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(table: PriceTable) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(
                {"fetched_at": table.fetched_at, "source": PRICE_URL, "prices": table.prices},
                fh, ensure_ascii=False,
            )
    except OSError:  # pragma: no cover - 書けなくても動作に影響しない
        pass


def load_prices(offline: bool = False, timeout: float = FETCH_TIMEOUT) -> PriceTable:
    """起動時に呼ぶ。最新価格 → キャッシュ → 参考価格 の順に解決する。"""
    if not offline:
        try:
            prices = fetch_prices(timeout=timeout)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError,
                TimeoutError) as exc:
            cached = _read_cache()
            if cached is not None:
                cached.error = str(exc)
                return cached
            return PriceTable(prices=dict(BUILTIN_PRICES), origin="builtin", error=str(exc))
        table = PriceTable(prices=prices, origin="live", fetched_at=time.time())
        _write_cache(table)
        return table

    cached = _read_cache()
    if cached is not None:
        return cached
    return PriceTable(prices=dict(BUILTIN_PRICES), origin="builtin")


# --------------------------------------------------------------------------
# 選抜と表示
# --------------------------------------------------------------------------

def cheapest_models(table: PriceTable, kind: str, count: int = 3) -> List[ModelInfo]:
    """いま最も安いモデルを安い順に count 件返す。"""
    candidates = [m for m in ALL_MODELS if m.kind == kind]
    priced = []
    for model in candidates:
        if kind == "transcribe":
            rate = table.transcribe_cost_per_minute(model.name)
        else:
            rates = table.polish_cost_per_mtok(model.name)
            # 推敲は出力トークンが支配的なので、入力 1 : 出力 2 の重みで比較する
            rate = (rates[0] + rates[1] * 2) / 3 if rates else None
        if rate is not None:
            priced.append((rate, model))
    priced.sort(key=lambda item: (item[0], item[1].name))
    return [model for _, model in priced[:count]]


def selectable_models(table: PriceTable, kind: str, default: str,
                      count: int = 3) -> List[ModelInfo]:
    """画面に出す選択肢＝「既定のモデル」＋「いま安い順に count 件」。"""
    chosen: List[ModelInfo] = []
    for model in [m for m in ALL_MODELS if m.kind == kind and m.name == default]:
        chosen.append(model)
    for model in cheapest_models(table, kind, count):
        if all(model.name != m.name for m in chosen):
            chosen.append(model)
    return chosen


def format_price(table: PriceTable, model: ModelInfo) -> str:
    """選択肢に出す料金の文字列。"""
    if model.kind == "transcribe":
        rate = table.transcribe_cost_per_minute(model.name)
        if rate is None:
            return "料金不明"
        return f"音声1時間 約 ${rate * 60:.2f}"
    rates = table.polish_cost_per_mtok(model.name)
    if rates is None:
        return "料金不明"
    return f"入力 ${rates[0]:.2f} / 出力 ${rates[1]:.2f}（100万トークン）"


def format_choice(table: PriceTable, model: ModelInfo) -> str:
    """コンボボックスなどに出す 1 行。"""
    return f"{model.name}　｜　{format_price(table, model)}　｜　{model.note}"


def model_from_choice(choice: str) -> str:
    """format_choice() が作った文字列からモデル名を取り出す。"""
    return choice.split("｜")[0].strip().replace("　", "")


def format_estimate(table: PriceTable, transcribe_model: str, polish_model: Optional[str],
                    minutes: Optional[float], chars: Optional[float] = None) -> str:
    """この録音を処理したときの概算費用。"""
    if minutes is None and chars is None:
        return "概算費用: 不明（長さを取得できませんでした）"
    transcribe = table.estimate_transcribe(transcribe_model, minutes)
    polish = (
        table.estimate_polish(polish_model, chars=chars, minutes=minutes)
        if polish_model else None
    )
    parts = []
    total = 0.0
    if transcribe is not None:
        parts.append(f"文字起こし ${transcribe:.3f}")
        total += transcribe
    if polish is not None:
        parts.append(f"推敲 ${polish:.3f}")
        total += polish
    if not parts:
        return "概算費用: 不明"
    return f"概算費用: 約 ${total:.3f}（{' ＋ '.join(parts)}）"


def price_report(table: PriceTable) -> str:
    """CLI の --list-models で出す一覧。"""
    lines = [table.origin_label(), ""]
    lines.append("【文字起こしモデル】（音声 1 時間あたりの目安）")
    for model in TRANSCRIBE_MODELS:
        rate = table.transcribe_cost_per_minute(model.name)
        price = f"${rate * 60:>6.2f}" if rate is not None else "  不明 "
        lines.append(f"  {model.name:<26} {price}   {model.note}")
    cheap = cheapest_models(table, "transcribe")
    lines.append("  → 現時点で安い順: " + " / ".join(m.name for m in cheap))
    lines.append("")
    lines.append("【推敲モデル】（100 万トークンあたり 入力 / 出力）")
    for model in POLISH_MODELS:
        rates = table.polish_cost_per_mtok(model.name)
        price = f"${rates[0]:>6.2f} / ${rates[1]:>7.2f}" if rates else "        不明"
        lines.append(f"  {model.name:<26} {price}   {model.note}")
    cheap = cheapest_models(table, "polish")
    lines.append("  → 現時点で安い順: " + " / ".join(m.name for m in cheap))
    lines.append("")
    lines.append("※ 料金は音声入力ぶんの目安です。実際の請求額は文字数などで前後します。")
    return "\n".join(lines)
