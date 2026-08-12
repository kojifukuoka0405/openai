"""OpenAI の音声認識 API を使った文字起こし。

既定は `gpt-transcribe`（公開されている中で最も高精度な文字起こしモデル）。
利用できない場合は `gpt-4o-transcribe` → `whisper-1` の順に自動で切り替える。

長い音声は audio.py で分割して順に送り、直前の断片の末尾を `prompt` として
渡すことで、固有名詞や文体が継ぎ目でぶれにくいようにしている。
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from . import audio
from .audio import AudioChunk

# 高精度モデルから順に試す（前のモデルが使えない環境でも動くように）
DEFAULT_TRANSCRIBE_MODEL = "gpt-transcribe"
TRANSCRIBE_FALLBACKS = ["gpt-transcribe", "gpt-4o-transcribe", "whisper-1"]

# 継ぎ目の文脈として次の断片に渡す文字数
CONTEXT_TAIL_CHARS = 400

# 1 リクエストのタイムアウト（秒）。長い音声は時間がかかる
REQUEST_TIMEOUT = 900.0

# API に渡すが、モデルによっては受け付けない任意パラメータ
OPTIONAL_PARAMS = (
    "chunking_strategy", "keywords", "languages", "prompt",
    "temperature", "timestamp_granularities", "include", "language",
)

ProgressFn = Callable[[int, int, str], None]


class TranscriptionError(RuntimeError):
    """文字起こしに失敗したときのエラー。"""


@dataclass
class ChunkTranscript:
    index: int
    start: float
    duration: Optional[float]
    text: str


@dataclass
class Transcript:
    text: str
    model: str
    chunks: List[ChunkTranscript] = field(default_factory=list)
    duration: Optional[float] = None


def _extract_text(response: object) -> str:
    """API のレスポンスから本文を取り出す。"""
    if isinstance(response, str):
        return response.strip()
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text.strip()
    # diarized_json など: セグメントを話者付きで連結する
    segments = getattr(response, "segments", None)
    if segments:
        lines = []
        for seg in segments:
            seg_text = (getattr(seg, "text", "") or "").strip()
            if not seg_text:
                continue
            speaker = getattr(seg, "speaker", None)
            lines.append(f"{speaker}: {seg_text}" if speaker else seg_text)
        return "\n".join(lines)
    raise TranscriptionError(f"API の応答を解釈できませんでした: {type(response)!r}")


def _unsupported_param(message: str) -> Optional[str]:
    """エラーメッセージから「使えなかったパラメータ名」を推測する。"""
    lowered = message.lower()
    for name in OPTIONAL_PARAMS:
        if name in lowered and (
            "unsupported" in lowered or "not supported" in lowered
            or "unknown" in lowered or "invalid" in lowered
            or "unrecognized" in lowered
        ):
            return name
    return None


def _is_model_missing(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    return status == 404 or "model_not_found" in text or "does not exist" in text


def _create_transcription(client, model: str, path: Path, params: dict) -> str:
    """1 ファイルを 1 回の API 呼び出しで文字起こしする。

    モデルが受け付けないパラメータがあれば、それを外して自動で再試行する。
    """
    attempt = dict(params)
    for _ in range(len(OPTIONAL_PARAMS) + 1):
        with open(path, "rb") as fh:
            try:
                response = client.audio.transcriptions.create(
                    file=fh, model=model, timeout=REQUEST_TIMEOUT, **attempt
                )
            except Exception as exc:  # noqa: BLE001 - SDK の例外型に依存しない
                if _is_model_missing(exc):
                    raise
                dropped = _unsupported_param(str(exc))
                if dropped is None or dropped not in attempt:
                    raise
                attempt.pop(dropped)
                continue
        return _extract_text(response)
    raise TranscriptionError("API がリクエストを受け付けませんでした。")


def transcribe_chunks(
    client,
    chunks: Sequence[AudioChunk],
    model: str = DEFAULT_TRANSCRIBE_MODEL,
    language: Optional[str] = None,
    keywords: Optional[Sequence[str]] = None,
    prompt: Optional[str] = None,
    use_server_chunking: bool = True,
    progress: Optional[ProgressFn] = None,
    allow_fallback: bool = True,
) -> Transcript:
    """音声断片を順に文字起こしして 1 本のテキストにまとめる。"""
    if not chunks:
        raise TranscriptionError("文字起こしする音声がありません。")

    candidates = [model]
    if allow_fallback:
        candidates += [m for m in TRANSCRIBE_FALLBACKS if m != model]

    base: dict = {}
    if language:
        base["language"] = language
    if keywords:
        base["keywords"] = list(keywords)
    if use_server_chunking:
        # サーバ側で無音検出して区切ってもらう（長い音声で安定する）
        base["chunking_strategy"] = "auto"

    total = len(chunks)
    results: List[ChunkTranscript] = []
    carry = (prompt or "").strip()
    active_model = candidates[0]
    tried: List[str] = []

    index = 0
    while index < total:
        chunk = chunks[index]
        params = dict(base)
        # 話者分離モデルのときだけ、話者付きの応答形式を要求する
        params["response_format"] = "diarized_json" if "diarize" in active_model else "json"
        if carry:
            params["prompt"] = carry[-CONTEXT_TAIL_CHARS:]
        if progress is not None:
            progress(index, total, f"文字起こし中 [{index + 1}/{total}] ({active_model})")
        try:
            text = _create_transcription(client, active_model, chunk.path, params)
        except Exception as exc:  # noqa: BLE001
            if _is_model_missing(exc) and active_model in candidates:
                tried.append(active_model)
                remaining = [m for m in candidates if m not in tried]
                if remaining:
                    active_model = remaining[0]
                    if progress is not None:
                        progress(index, total, f"{tried[-1]} が使えないため {active_model} に切り替えます")
                    continue
            raise TranscriptionError(
                f"文字起こしに失敗しました（{chunk.path.name}）: {exc}"
            ) from exc

        results.append(ChunkTranscript(chunk.index, chunk.start, chunk.duration, text))
        if text:
            carry = text
        index += 1

    if progress is not None:
        progress(total, total, "文字起こし完了")

    full = "\n".join(r.text for r in results if r.text).strip()
    durations = [c.duration for c in chunks if c.duration is not None]
    return Transcript(
        text=full,
        model=active_model,
        chunks=results,
        duration=sum(durations) if durations else None,
    )


def transcribe_audio_file(
    client,
    path: Path,
    model: str = DEFAULT_TRANSCRIBE_MODEL,
    language: Optional[str] = None,
    keywords: Optional[Sequence[str]] = None,
    prompt: Optional[str] = None,
    max_seconds: float = audio.DEFAULT_MAX_SECONDS,
    progress: Optional[ProgressFn] = None,
) -> Transcript:
    """音声ファイル 1 つを（必要なら分割して）文字起こしする。"""
    path = Path(path)
    audio.check_supported(path)

    def notify(msg: str) -> None:
        if progress is not None:
            progress(0, 1, msg)

    with tempfile.TemporaryDirectory(prefix="audio_transcriber_") as tmp:
        chunks = audio.prepare_chunks(
            path, Path(tmp), max_seconds=max_seconds, progress=notify
        )
        if len(chunks) > 1:
            notify(f"{len(chunks)} 個に分割しました")
        return transcribe_chunks(
            client,
            chunks,
            model=model,
            language=language,
            keywords=keywords,
            prompt=prompt,
            progress=progress,
        )
