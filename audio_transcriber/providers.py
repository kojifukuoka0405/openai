"""文字起こしを行う各社 API への接続。

1 つの音声断片を 1 回の API 呼び出しで文字起こしする関数を、プロバイダごとに
用意している。呼び出し側（transcribe.py）はモデル ID を渡すだけでよい。

モデル ID は `プロバイダ/モデル名` 形式（例: `groq/whisper-large-v3-turbo`）。
接頭辞が無いものは OpenAI として扱う（例: `gpt-transcribe`）。

追加の依存は増やさず、OpenAI 互換のものは openai SDK、それ以外は標準ライブラリの
urllib で REST を直接叩いている。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence

REQUEST_TIMEOUT = 900.0
DEFAULT_MAX_CHUNK_BYTES = 24 * 1024 * 1024


class ProviderError(RuntimeError):
    """プロバイダ呼び出しに失敗したときのエラー。"""


@dataclass(frozen=True)
class Provider:
    """API キーの取り回しなど、プロバイダごとの情報。"""

    key: str                       # 内部識別子
    label: str                     # 画面表示名
    env_var: str                   # API キーの環境変数
    signup_url: str                # キー発行ページ
    base_url: Optional[str] = None  # OpenAI 互換のときのエンドポイント
    openai_compatible: bool = False
    max_chunk_bytes: int = DEFAULT_MAX_CHUNK_BYTES


PROVIDERS: Dict[str, Provider] = {
    "openai": Provider(
        "openai", "OpenAI", "OPENAI_API_KEY",
        "https://platform.openai.com/api-keys", openai_compatible=True,
    ),
    "gemini": Provider(
        "gemini", "Google Gemini", "GEMINI_API_KEY",
        "https://aistudio.google.com/apikey",
        # インライン送信は base64 化で 4/3 に膨らむため、少し小さめに切る
        max_chunk_bytes=13 * 1024 * 1024,
    ),
    "groq": Provider(
        "groq", "Groq", "GROQ_API_KEY", "https://console.groq.com/keys",
        base_url="https://api.groq.com/openai/v1", openai_compatible=True,
    ),
    "elevenlabs": Provider(
        "elevenlabs", "ElevenLabs", "ELEVENLABS_API_KEY",
        "https://elevenlabs.io/app/settings/api-keys",
    ),
    "deepgram": Provider(
        "deepgram", "Deepgram", "DEEPGRAM_API_KEY", "https://console.deepgram.com/",
    ),
    "assemblyai": Provider(
        "assemblyai", "AssemblyAI", "ASSEMBLYAI_API_KEY",
        "https://www.assemblyai.com/app/api-keys",
    ),
}

DEFAULT_PROVIDER = "openai"


def provider_of(model_id: str) -> str:
    """モデル ID からプロバイダ名を取り出す。"""
    if "/" in model_id:
        head = model_id.split("/", 1)[0]
        if head in PROVIDERS:
            return head
    return DEFAULT_PROVIDER


def bare_model(model_id: str) -> str:
    """API に渡す実際のモデル名（接頭辞を外したもの）。"""
    if "/" in model_id and model_id.split("/", 1)[0] in PROVIDERS:
        return model_id.split("/", 1)[1]
    return model_id


def get_provider(model_id: str) -> Provider:
    return PROVIDERS[provider_of(model_id)]


def resolve_key(model_id: str, api_keys: Optional[Dict[str, str]] = None) -> Optional[str]:
    """このモデルに使う API キー（明示指定 → 環境変数 → 設定ファイル）。"""
    from .config import stored_keys      # 循環 import を避けるため関数内で読む

    provider = get_provider(model_id)
    candidates = [
        (api_keys or {}).get(provider.key),
        os.environ.get(provider.env_var),
        stored_keys().get(provider.key),
    ]
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def missing_key_message(model_id: str) -> str:
    provider = get_provider(model_id)
    return (
        f"{provider.label} の API キーが設定されていません。\n"
        f"環境変数 {provider.env_var} に設定するか、アプリのキー欄に入力してください。\n"
        f"キーは {provider.signup_url} で発行できます。"
    )


# --------------------------------------------------------------------------
# HTTP の下ごしらえ
# --------------------------------------------------------------------------

def _http(url: str, *, data: Optional[bytes] = None, headers: Optional[dict] = None,
          method: str = "POST", timeout: float = REQUEST_TIMEOUT) -> dict:
    """JSON を返す HTTP 呼び出し。エラー本文もそのまま見せる。"""
    request = urllib.request.Request(url, data=data, method=method,
                                     headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise ProviderError(f"HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"接続に失敗しました: {exc}") from exc
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProviderError("応答を解釈できませんでした") from exc


def _multipart(fields: Dict[str, str], file_field: str, path: Path) -> tuple:
    """multipart/form-data の本文を組み立てる。"""
    boundary = f"----audio-transcriber-{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{value}\r\n".encode("utf-8")
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
        f"filename=\"{path.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode("utf-8")
    )
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _audio_mime(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0]
    if mime and mime.startswith("audio"):
        return mime
    return {
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
        ".mp4": "audio/mp4", ".flac": "audio/flac", ".ogg": "audio/ogg",
        ".oga": "audio/ogg", ".webm": "audio/webm", ".mpga": "audio/mpeg",
        ".mpeg": "audio/mpeg",
    }.get(path.suffix.lower(), "audio/mpeg")


def _instruction(language: Optional[str], keywords: Optional[Sequence[str]],
                 context: Optional[str]) -> str:
    """文字起こし専用モデル以外（Gemini など）に渡す指示文。"""
    lines = [
        "この音声を一言一句そのまま文字起こししてください。",
        "話されている言語のまま出力し、翻訳・要約・説明・見出しは一切加えないでください。",
        "出力は文字起こしの本文のみとし、前置きやコードブロックは付けないでください。",
        "聞き取れない箇所は [不明] と書いてください。",
    ]
    if language:
        lines.append(f"音声の言語は {language} です。")
    if keywords:
        lines.append("次の固有名詞・専門用語が出てきます: " + "、".join(keywords))
    if context:
        lines.append(
            "直前の音声の文字起こしは次のとおりです（重複して出力しないでください）:\n"
            + context
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# プロバイダごとの実装
# --------------------------------------------------------------------------

def gemini_transcribe(path: Path, model: str, api_key: str, language=None,
                      keywords=None, context=None, timeout=REQUEST_TIMEOUT) -> str:
    """Gemini（generateContent）に音声を直接渡して文字起こしする。"""
    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": _instruction(language, keywords, context)},
                {"inline_data": {
                    "mime_type": _audio_mime(path),
                    "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                }},
            ],
        }],
        "generationConfig": {"temperature": 0.0},
    }
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    data = _http(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        timeout=timeout,
    )
    candidates = data.get("candidates") or []
    if not candidates:
        feedback = data.get("promptFeedback") or {}
        raise ProviderError(f"Gemini が応答を返しませんでした: {feedback or data}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise ProviderError("Gemini の応答が空でした")
    return text


def elevenlabs_transcribe(path: Path, model: str, api_key: str, language=None,
                          keywords=None, context=None, timeout=REQUEST_TIMEOUT) -> str:
    """ElevenLabs Scribe で文字起こしする。"""
    fields = {"model_id": model, "diarize": "false", "tag_audio_events": "false"}
    if language:
        fields["language_code"] = language
    body, content_type = _multipart(fields, "file", path)
    data = _http(
        "https://api.elevenlabs.io/v1/speech-to-text",
        data=body,
        headers={"Content-Type": content_type, "xi-api-key": api_key},
        timeout=timeout,
    )
    text = (data.get("text") or "").strip()
    if not text:
        raise ProviderError("ElevenLabs の応答が空でした")
    return text


def deepgram_transcribe(path: Path, model: str, api_key: str, language=None,
                        keywords=None, context=None, timeout=REQUEST_TIMEOUT) -> str:
    """Deepgram（nova 系）で文字起こしする。"""
    query = [f"model={model}", "smart_format=true", "punctuate=true"]
    if language:
        query.append(f"language={language}")
    else:
        query.append("detect_language=true")
    for word in (keywords or []):
        query.append("keyterm=" + urllib.parse.quote(word))
    data = _http(
        "https://api.deepgram.com/v1/listen?" + "&".join(query),
        data=path.read_bytes(),
        headers={"Authorization": f"Token {api_key}", "Content-Type": _audio_mime(path)},
        timeout=timeout,
    )
    try:
        alternatives = data["results"]["channels"][0]["alternatives"]
    except (KeyError, IndexError) as exc:
        raise ProviderError(f"Deepgram の応答を解釈できませんでした: {data}") from exc
    text = (alternatives[0].get("transcript") or "").strip() if alternatives else ""
    if not text:
        raise ProviderError("Deepgram の応答が空でした")
    return text


def assemblyai_transcribe(path: Path, model: str, api_key: str, language=None,
                          keywords=None, context=None, timeout=REQUEST_TIMEOUT,
                          poll_seconds: float = 3.0) -> str:
    """AssemblyAI（アップロード → 変換 → 完了待ち）で文字起こしする。"""
    headers = {"authorization": api_key}
    upload = _http(
        "https://api.assemblyai.com/v2/upload",
        data=path.read_bytes(),
        headers={**headers, "Content-Type": "application/octet-stream"},
        timeout=timeout,
    )
    audio_url = upload.get("upload_url")
    if not audio_url:
        raise ProviderError("AssemblyAI へのアップロードに失敗しました")

    request: dict = {"audio_url": audio_url, "speech_model": model, "punctuate": True}
    if language:
        request["language_code"] = language
    else:
        request["language_detection"] = True
    if keywords:
        request["word_boost"] = list(keywords)
    created = _http(
        "https://api.assemblyai.com/v2/transcript",
        data=json.dumps(request).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        timeout=timeout,
    )
    transcript_id = created.get("id")
    if not transcript_id:
        raise ProviderError(f"AssemblyAI がジョブを受け付けませんでした: {created}")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = _http(
            f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
            headers=headers, method="GET", timeout=60,
        )
        state = status.get("status")
        if state == "completed":
            text = (status.get("text") or "").strip()
            if not text:
                raise ProviderError("AssemblyAI の応答が空でした")
            return text
        if state == "error":
            raise ProviderError(f"AssemblyAI でエラー: {status.get('error')}")
        time.sleep(poll_seconds)
    raise ProviderError("AssemblyAI の処理がタイムアウトしました")


REST_PROVIDERS = {
    "gemini": gemini_transcribe,
    "elevenlabs": elevenlabs_transcribe,
    "deepgram": deepgram_transcribe,
    "assemblyai": assemblyai_transcribe,
}


def transcribe_chunk(model_id: str, path: Path, api_key: str, language=None,
                     keywords=None, context=None, timeout: float = REQUEST_TIMEOUT) -> str:
    """OpenAI 互換以外のプロバイダで 1 断片を文字起こしする。"""
    provider = provider_of(model_id)
    handler = REST_PROVIDERS.get(provider)
    if handler is None:
        raise ProviderError(f"このプロバイダには対応していません: {provider}")
    return handler(
        Path(path), bare_model(model_id), api_key,
        language=language, keywords=keywords, context=context, timeout=timeout,
    )
