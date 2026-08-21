"""audio_transcriber: 音声ファイルを高精度に文字起こしし、推敲版も出力するツール。

- 文字起こし: OpenAI の `gpt-transcribe`（公開されている最高精度クラスの
  音声認識モデル）。使えない環境では `gpt-4o-transcribe` → `whisper-1` に自動で
  切り替える。
- 推敲: `gpt-5.6` 系で、内容を変えずにフィラー削除・句読点・段落・表記ゆれを整える。
- MP3 / WAV は標準ライブラリだけで分割できるので、長い録音でも追加ツールなしで動く。
"""

from .audio import AudioChunk, AudioError, prepare_chunks, probe_duration
from .core import (
    Options,
    Result,
    TranscriberError,
    format_duration,
    make_client,
    transcribe_and_polish,
    write_outputs,
)
from .polish import DEFAULT_POLISH_MODEL, PolishError, polish_text
from .pricing import (
    POLISH_MODELS,
    TRANSCRIBE_MODELS,
    PriceTable,
    cheapest_models,
    load_prices,
    price_report,
    selectable_models,
)
from .transcribe import (
    DEFAULT_TRANSCRIBE_MODEL,
    Transcript,
    TranscriptionError,
    transcribe_audio_file,
)

__all__ = [
    "AudioChunk",
    "AudioError",
    "DEFAULT_POLISH_MODEL",
    "DEFAULT_TRANSCRIBE_MODEL",
    "Options",
    "POLISH_MODELS",
    "PolishError",
    "PriceTable",
    "Result",
    "TRANSCRIBE_MODELS",
    "TranscriberError",
    "Transcript",
    "TranscriptionError",
    "cheapest_models",
    "format_duration",
    "load_prices",
    "make_client",
    "polish_text",
    "prepare_chunks",
    "price_report",
    "probe_duration",
    "selectable_models",
    "transcribe_and_polish",
    "transcribe_audio_file",
    "write_outputs",
]
__version__ = "0.1.0"
