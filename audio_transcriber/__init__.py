"""audio_transcriber: 音声ファイルを高精度に文字起こしし、推敲版も出力するツール。

- 文字起こし: 6 社のモデルから選べる（OpenAI / Google Gemini / Groq /
  AssemblyAI / ElevenLabs / Deepgram）。既定は最高精度の `gpt-transcribe`、
  起動時に取得した実価格で「そのとき最も安いモデル」も選択肢に並ぶ。
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
from .providers import PROVIDERS, ProviderError, provider_of
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
    "PROVIDERS",
    "POLISH_MODELS",
    "PolishError",
    "PriceTable",
    "ProviderError",
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
    "provider_of",
    "probe_duration",
    "selectable_models",
    "transcribe_and_polish",
    "transcribe_audio_file",
    "write_outputs",
]
__version__ = "0.1.0"
