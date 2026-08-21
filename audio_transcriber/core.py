"""音声ファイル → 文字起こし＋推敲版 の一連の処理。

    from audio_transcriber import transcribe_and_polish
    result = transcribe_and_polish("会議.mp3")
    print(result.transcript)   # そのままの文字起こし
    print(result.polished)     # 推敲した版
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import audio, polish as polish_mod, providers, transcribe as transcribe_mod
from .config import resolve_api_key

# 進捗コールバック: (進捗率 0-100, 100, メッセージ)
ProgressFn = Callable[[int, int, str], None]

# 全体の進捗のうち、文字起こしが占める割合
TRANSCRIBE_SHARE = 0.75


class TranscriberError(RuntimeError):
    """アプリ全体で使うエラー。"""


@dataclass
class Options:
    """処理のオプション。"""

    transcribe_model: str = transcribe_mod.DEFAULT_TRANSCRIBE_MODEL
    polish_model: str = polish_mod.DEFAULT_POLISH_MODEL
    language: Optional[str] = None                    # 例: "ja"（未指定なら自動判定）
    keywords: Sequence[str] = field(default_factory=tuple)  # 固有名詞・専門用語
    style: str = "readable"                           # readable / verbatim / article
    extra_instructions: Optional[str] = None          # 推敲への追加指示
    do_polish: bool = True
    max_seconds: float = audio.DEFAULT_MAX_SECONDS


@dataclass
class Result:
    """処理結果。"""

    source: Path
    transcript: str
    polished: str
    transcribe_model: str
    polish_model: Optional[str]
    duration: Optional[float]
    chunk_count: int
    written: List[Path] = field(default_factory=list)


def make_client(api_key: Optional[str] = None, base_url: Optional[str] = None):
    """OpenAI クライアントを作る。"""
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - 依存未インストール時
        raise TranscriberError(
            "openai パッケージが見つかりません。次を実行してください:\n"
            "  pip install openai"
        ) from exc

    key = resolve_api_key(api_key)
    if not key:
        raise TranscriberError(
            "OpenAI の API キーが設定されていません。\n"
            "環境変数 OPENAI_API_KEY に設定するか、アプリのキー欄に入力してください。\n"
            "キーは https://platform.openai.com/api-keys で発行できます。"
        )
    kwargs = {"api_key": key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _scaled(progress: Optional[ProgressFn], lo: float, hi: float) -> transcribe_mod.ProgressFn:
    """下位モジュールの (done, total, msg) を全体の進捗率に変換する。"""

    def inner(done: int, total: int, message: str) -> None:
        if progress is None:
            return
        ratio = (done / total) if total else 0.0
        percent = int(round(lo + (hi - lo) * min(max(ratio, 0.0), 1.0)))
        progress(percent, 100, message)

    return inner


def transcribe_and_polish(
    path: Path | str,
    options: Optional[Options] = None,
    client=None,
    api_key: Optional[str] = None,
    progress: Optional[ProgressFn] = None,
    api_keys: Optional[Dict[str, str]] = None,
) -> Result:
    """音声ファイルを文字起こしし、推敲版も作る。

    文字起こしは選んだモデルのプロバイダ（OpenAI / Gemini / Groq …）を使い、
    推敲は OpenAI を使う。必要なキーだけを要求する。
    """
    options = options or Options()
    path = Path(path)
    audio.check_supported(path)

    # OpenAI のクライアントは「OpenAI で文字起こしする」か「推敲する」ときだけ要る
    needs_openai = options.do_polish or (
        providers.provider_of(options.transcribe_model) == providers.DEFAULT_PROVIDER
    )
    if client is None and needs_openai:
        client = make_client(api_key or (api_keys or {}).get("openai"))

    if progress is not None:
        progress(0, 100, "音声を確認しています…")

    transcript = transcribe_mod.transcribe_audio_file(
        client,
        path,
        model=options.transcribe_model,
        language=options.language,
        keywords=list(options.keywords) or None,
        max_seconds=options.max_seconds,
        progress=_scaled(progress, 2, TRANSCRIBE_SHARE * 100),
        api_keys=api_keys,
    )

    if not transcript.text.strip():
        raise TranscriberError(
            "文字起こし結果が空でした。無音のファイルでないか確認してください。"
        )

    polished = ""
    polish_model: Optional[str] = None
    if options.do_polish:
        polish_model = options.polish_model
        polished = polish_mod.polish_text(
            client,
            transcript.text,
            model=options.polish_model,
            style=options.style,
            extra_instructions=options.extra_instructions,
            progress=_scaled(progress, TRANSCRIBE_SHARE * 100, 100),
        )

    if progress is not None:
        progress(100, 100, "完了")

    return Result(
        source=path,
        transcript=transcript.text,
        polished=polished,
        transcribe_model=transcript.model,
        polish_model=polish_model,
        duration=transcript.duration,
        chunk_count=len(transcript.chunks),
    )


def write_outputs(
    result: Result,
    outdir: Optional[Path | str] = None,
    basename: Optional[str] = None,
) -> Tuple[Path, Optional[Path]]:
    """文字起こしと推敲版をファイルに書き出す。

    - `<名前>_文字起こし.txt`
    - `<名前>_推敲版.md`
    """
    directory = Path(outdir) if outdir else result.source.parent
    directory.mkdir(parents=True, exist_ok=True)
    stem = basename or result.source.stem

    raw_path = directory / f"{stem}_文字起こし.txt"
    raw_path.write_text(result.transcript.rstrip() + "\n", encoding="utf-8")

    polished_path: Optional[Path] = None
    if result.polished.strip():
        polished_path = directory / f"{stem}_推敲版.md"
        polished_path.write_text(result.polished.rstrip() + "\n", encoding="utf-8")

    result.written = [p for p in (raw_path, polished_path) if p is not None]
    return raw_path, polished_path


def format_duration(seconds: Optional[float]) -> str:
    """秒数を h:mm:ss 形式にする。"""
    if seconds is None:
        return "不明"
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
