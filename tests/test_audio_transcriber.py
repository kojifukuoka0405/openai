"""音声分割・文字起こし・推敲のテスト。

API は呼ばず、合成した音声ファイルと偽クライアントで検証する。
"""

import base64
import io
import json
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from audio_transcriber import audio, core, polish, pricing, providers, transcribe

# --------------------------------------------------------------------------
# 合成データ
# --------------------------------------------------------------------------

MP3_HEADER = b"\xff\xfb\x90\xc0"      # MPEG1 Layer III / 128kbps / 44.1kHz / mono
MP3_FRAME_LEN = 417
MP3_FRAME_SEC = 1152 / 44100


def make_mp3(path: Path, frames: int, id3: bool = False) -> Path:
    body = b"".join(MP3_HEADER + bytes(MP3_FRAME_LEN - 4) for _ in range(frames))
    tag = b""
    if id3:
        payload = b"\x00" * 100
        tag = b"ID3\x04\x00\x00" + bytes([0, 0, 0, len(payload)]) + payload
    path.write_bytes(tag + body)
    return path


def make_wav(path: Path, seconds: float, rate: int = 8000, channels: int = 1,
             gap: tuple = None) -> Path:
    """一定の音量が続く WAV を作る。gap=(開始秒, 終了秒) の区間だけ無音にする。"""
    total = int(rate * seconds)
    samples = bytearray()
    for i in range(total):
        t = i / rate
        quiet = gap is not None and gap[0] <= t < gap[1]
        value = 0 if quiet else 8000
        sign = 1 if (i % 2) else -1
        samples += (sign * value).to_bytes(2, "little", signed=True) * channels
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(samples))
    return path


# --------------------------------------------------------------------------
# MP3
# --------------------------------------------------------------------------

def test_mp3_frame_parsing(tmp_path):
    src = make_mp3(tmp_path / "a.mp3", 100)
    data = src.read_bytes()
    frames = audio.iter_mp3_frames(data)
    assert len(frames) == 100
    assert all(f.length == MP3_FRAME_LEN for f in frames)
    assert audio.mp3_duration(src) == pytest.approx(100 * MP3_FRAME_SEC)


def test_mp3_skips_id3v2(tmp_path):
    src = make_mp3(tmp_path / "tagged.mp3", 20, id3=True)
    assert len(audio.iter_mp3_frames(src.read_bytes())) == 20


def test_split_mp3_keeps_frame_boundaries(tmp_path):
    src = make_mp3(tmp_path / "long.mp3", 300)
    chunks = audio.split_mp3(src, tmp_path / "out", max_seconds=1.0)

    assert len(chunks) > 1
    rebuilt = b""
    total_frames = 0
    for chunk in chunks:
        data = chunk.path.read_bytes()
        frames = audio.iter_mp3_frames(data)
        assert frames, "各断片が MP3 として解析できること"
        assert chunk.duration <= 1.0 + 1e-6
        total_frames += len(frames)
        rebuilt += data
    assert total_frames == 300
    assert rebuilt == src.read_bytes()          # 音声データが欠けない

    starts = [c.start for c in chunks]
    assert starts == sorted(starts)
    assert starts[0] == 0.0


def test_split_mp3_respects_byte_limit(tmp_path):
    src = make_mp3(tmp_path / "big.mp3", 200)
    limit = MP3_FRAME_LEN * 10
    chunks = audio.split_mp3(src, tmp_path / "out", max_bytes=limit, max_seconds=1e9)
    assert len(chunks) == 20
    assert all(c.path.stat().st_size <= limit for c in chunks)


# --------------------------------------------------------------------------
# WAV
# --------------------------------------------------------------------------

def test_wav_duration_and_split(tmp_path):
    src = make_wav(tmp_path / "a.wav", 10.0)
    assert audio.wav_duration(src) == pytest.approx(10.0)

    chunks = audio.split_wav(src, tmp_path / "out", max_seconds=3.0)
    assert len(chunks) >= 4

    total = 0.0
    for chunk in chunks:
        with wave.open(str(chunk.path), "rb") as wf:
            assert wf.getframerate() == 8000
            assert wf.getnchannels() == 1
            total += wf.getnframes() / 8000
        assert chunk.duration <= 3.0 + 1e-6
    assert total == pytest.approx(10.0)          # 合計が元の長さと一致


def test_wav_split_cuts_at_quiet_point(tmp_path):
    # 2.4〜2.6 秒だけ無音。3 秒目標でも、その無音部分で切れてほしい
    src = make_wav(tmp_path / "b.wav", 12.0, gap=(2.4, 2.6))
    chunks = audio.split_wav(src, tmp_path / "out", max_seconds=3.0)
    first_end = chunks[0].duration
    assert 2.35 <= first_end <= 2.65
    assert all(c.duration <= 3.0 + 1e-6 for c in chunks), "上限は超えない"


def test_split_wav_roundtrip_keeps_all_samples(tmp_path):
    src = make_wav(tmp_path / "c.wav", 7.0, gap=(3.0, 3.2))
    chunks = audio.split_wav(src, tmp_path / "out", max_seconds=2.0)

    rebuilt = b""
    for chunk in chunks:
        with wave.open(str(chunk.path), "rb") as wf:
            rebuilt += wf.readframes(wf.getnframes())
    with wave.open(str(src), "rb") as wf:
        original = wf.readframes(wf.getnframes())
    assert rebuilt == original          # 欠落も重複もない


def test_split_wav_rejects_non_pcm(tmp_path):
    bogus = tmp_path / "broken.wav"
    bogus.write_bytes(b"not a wav file at all")
    with pytest.raises(audio.AudioError):
        audio.split_wav(bogus, tmp_path / "out")


# --------------------------------------------------------------------------
# 入力チェックと分割の入口
# --------------------------------------------------------------------------

def test_check_supported(tmp_path):
    ok = make_mp3(tmp_path / "a.mp3", 5)
    audio.check_supported(ok)

    bad = tmp_path / "memo.txt"
    bad.write_text("hello")
    with pytest.raises(audio.AudioError):
        audio.check_supported(bad)
    with pytest.raises(audio.AudioError):
        audio.check_supported(tmp_path / "missing.mp3")


def test_prepare_chunks_returns_original_when_small(tmp_path):
    src = make_mp3(tmp_path / "small.mp3", 50)
    chunks = audio.prepare_chunks(src, tmp_path / "work")
    assert len(chunks) == 1
    assert chunks[0].path == src
    assert chunks[0].temporary is False


def test_prepare_chunks_splits_long_audio(tmp_path):
    src = make_wav(tmp_path / "long.wav", 20.0)
    chunks = audio.prepare_chunks(
        src, tmp_path / "work", max_seconds=5.0, allow_ffmpeg=False
    )
    assert len(chunks) >= 4
    assert all(c.temporary for c in chunks)


def test_prepare_chunks_needs_ffmpeg_for_other_formats(tmp_path):
    src = tmp_path / "voice.m4a"
    src.write_bytes(b"\x00" * 1024)
    with pytest.raises(audio.AudioError, match="ffmpeg"):
        audio.prepare_chunks(
            src, tmp_path / "work", max_bytes=10, max_seconds=1.0, allow_ffmpeg=False
        )


# --------------------------------------------------------------------------
# 偽クライアント
# --------------------------------------------------------------------------

class FakeApiError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class FakeTranscriptions:
    def __init__(self, errors=None):
        self.calls = []
        self.errors = list(errors or [])

    def create(self, *, file, model, timeout=None, **kwargs):
        self.calls.append({"model": model, "file": Path(file.name).name, **kwargs})
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        return SimpleNamespace(text=f"テキスト{len(self.calls)}")


class FakeResponses:
    def __init__(self, errors=None, status="completed"):
        self.calls = []
        self.errors = list(errors or [])
        self.status = status

    def create(self, *, model, instructions, input, max_output_tokens, **kwargs):
        self.calls.append(
            {"model": model, "input": input, "budget": max_output_tokens}
        )
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        status = self.status if len(self.calls) == 1 else "completed"
        return SimpleNamespace(output_text=f"推敲{len(self.calls)}", status=status)


class FakeChat:
    def __init__(self):
        self.calls = []
        self.completions = self

    def create(self, *, model, messages, **kwargs):
        self.calls.append({"model": model, "messages": messages})
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="chat推敲"), finish_reason="stop"
                )
            ]
        )


class FakeClient:
    def __init__(self, transcriptions=None, responses=None):
        self.audio = SimpleNamespace(transcriptions=transcriptions or FakeTranscriptions())
        self.responses = responses or FakeResponses()
        self.chat = FakeChat()


# --------------------------------------------------------------------------
# 文字起こし
# --------------------------------------------------------------------------

def _chunks(tmp_path, count):
    made = []
    for i in range(count):
        path = make_mp3(tmp_path / f"part{i}.mp3", 5)
        made.append(audio.AudioChunk(path, i, i * 1.0, 1.0))
    return made


def test_transcribe_chunks_joins_text_and_carries_context(tmp_path):
    fake = FakeTranscriptions()
    client = FakeClient(fake)
    result = transcribe.transcribe_chunks(
        client, _chunks(tmp_path, 3), language="ja", keywords=["Claude"]
    )

    assert result.text == "テキスト1\nテキスト2\nテキスト3"
    assert result.model == transcribe.DEFAULT_TRANSCRIBE_MODEL
    assert len(result.chunks) == 3
    assert result.duration == pytest.approx(3.0)

    assert "prompt" not in fake.calls[0]                 # 最初は文脈なし
    assert fake.calls[1]["prompt"] == "テキスト1"        # 直前の結果を引き継ぐ
    assert fake.calls[2]["prompt"] == "テキスト2"
    assert fake.calls[0]["language"] == "ja"
    assert fake.calls[0]["keywords"] == ["Claude"]
    assert fake.calls[0]["chunking_strategy"] == "auto"


def test_transcribe_drops_unsupported_parameter(tmp_path):
    fake = FakeTranscriptions(errors=[FakeApiError("Unknown parameter: 'keywords'.")])
    client = FakeClient(fake)
    result = transcribe.transcribe_chunks(
        client, _chunks(tmp_path, 1), keywords=["ABC"]
    )

    assert result.text == "テキスト2"       # 2 回目の呼び出しで成功
    assert "keywords" in fake.calls[0]
    assert "keywords" not in fake.calls[1]     # 外して再送している


def test_transcribe_falls_back_to_other_model(tmp_path):
    fake = FakeTranscriptions(
        errors=[FakeApiError("The model `gpt-transcribe` does not exist", 404)]
    )
    client = FakeClient(fake)
    result = transcribe.transcribe_chunks(client, _chunks(tmp_path, 1))

    assert fake.calls[0]["model"] == "gpt-transcribe"
    assert fake.calls[1]["model"] == "gpt-4o-transcribe"
    assert result.model == "gpt-4o-transcribe"


def test_transcribe_reports_failure(tmp_path):
    fake = FakeTranscriptions(errors=[FakeApiError("rate limited", 429)])
    client = FakeClient(fake)
    with pytest.raises(transcribe.TranscriptionError):
        transcribe.transcribe_chunks(client, _chunks(tmp_path, 1))


def test_extract_text_handles_speaker_segments():
    response = SimpleNamespace(
        segments=[
            SimpleNamespace(speaker="A", text="おはよう"),
            SimpleNamespace(speaker="B", text="おはようございます"),
        ]
    )
    assert transcribe._extract_text(response) == "A: おはよう\nB: おはようございます"


# --------------------------------------------------------------------------
# 推敲
# --------------------------------------------------------------------------

def test_split_for_polish_breaks_on_sentences():
    text = "".join(f"これは文{i}です。" for i in range(50))
    chunks = polish.split_for_polish(text, chunk_chars=100)
    assert len(chunks) > 1
    assert "".join(chunks) == text                   # 欠落しない
    assert all(c.endswith("。") for c in chunks)     # 文の途中で切らない


def test_split_for_polish_short_text():
    assert polish.split_for_polish("短い文です。") == ["短い文です。"]
    assert polish.split_for_polish("   ") == []


def test_polish_text_uses_context_and_joins():
    responses = FakeResponses()
    client = FakeClient(responses=responses)
    text = "".join(f"あのー、これは文{i}です。" for i in range(400))

    result = polish.polish_text(client, text, model="gpt-5.6-sol")

    assert len(responses.calls) > 1
    assert result == "\n\n".join(f"推敲{i + 1}" for i in range(len(responses.calls)))
    assert "直前までの推敲済みテキスト" not in responses.calls[0]["input"]
    assert "推敲1" in responses.calls[1]["input"]     # 文体をそろえる文脈
    assert all(c["model"] == "gpt-5.6-sol" for c in responses.calls)


def test_polish_falls_back_to_chat_completions():
    responses = FakeResponses(errors=[RuntimeError("responses API 利用不可")])
    client = FakeClient(responses=responses)
    assert polish.polish_text(client, "えー、テストです。") == "chat推敲"
    assert client.chat.calls


def test_polish_retries_when_output_truncated():
    responses = FakeResponses(status="incomplete")
    client = FakeClient(responses=responses)
    result = polish.polish_text(client, "テストです。")
    assert result == "推敲2"
    assert responses.calls[1]["budget"] > responses.calls[0]["budget"]


def test_polish_switches_model_when_missing():
    responses = FakeResponses(
        errors=[FakeApiError("model_not_found", 404)]
    )
    client = FakeClient(responses=responses)
    polish.polish_text(client, "テストです。", model="gpt-5.6-sol")
    assert responses.calls[0]["model"] == "gpt-5.6-sol"
    assert responses.calls[1]["model"] == "gpt-5.4"


def test_build_instructions_includes_style_and_extra():
    text = polish.build_instructions("article", extra="敬体に統一して")
    assert "見出し" in text
    assert "敬体に統一して" in text


# --------------------------------------------------------------------------
# パイプラインと出力
# --------------------------------------------------------------------------

def test_transcribe_and_polish_end_to_end(tmp_path):
    src = make_mp3(tmp_path / "会議.mp3", 30)
    client = FakeClient()
    events = []

    result = core.transcribe_and_polish(
        src,
        core.Options(language="ja"),
        client=client,
        progress=lambda done, total, msg: events.append((done, msg)),
    )

    assert result.transcript == "テキスト1"
    assert result.polished == "推敲1"
    assert result.transcribe_model == transcribe.DEFAULT_TRANSCRIBE_MODEL
    assert result.polish_model == polish.DEFAULT_POLISH_MODEL
    assert events[-1][0] == 100
    assert [d for d, _ in events] == sorted(d for d, _ in events)   # 進捗が戻らない

    raw_path, polished_path = core.write_outputs(result, tmp_path / "out")
    assert raw_path.name == "会議_文字起こし.txt"
    assert polished_path.name == "会議_推敲版.md"
    assert raw_path.read_text(encoding="utf-8") == "テキスト1\n"
    assert polished_path.read_text(encoding="utf-8") == "推敲1\n"
    assert result.written == [raw_path, polished_path]


def test_transcribe_and_polish_can_skip_polishing(tmp_path):
    src = make_mp3(tmp_path / "a.mp3", 10)
    client = FakeClient()
    result = core.transcribe_and_polish(
        src, core.Options(do_polish=False), client=client
    )
    assert result.polished == ""
    assert result.polish_model is None

    raw_path, polished_path = core.write_outputs(result, tmp_path / "out")
    assert raw_path.exists()
    assert polished_path is None


def test_empty_transcript_raises(tmp_path):
    src = make_mp3(tmp_path / "silent.mp3", 10)

    class Silent(FakeTranscriptions):
        def create(self, **kwargs):
            super().create(**kwargs)
            return SimpleNamespace(text="   ")

    client = FakeClient(Silent())
    with pytest.raises(core.TranscriberError, match="空"):
        core.transcribe_and_polish(src, client=client)


def test_format_duration():
    assert core.format_duration(None) == "不明"
    assert core.format_duration(75) == "1:15"
    assert core.format_duration(3725) == "1:02:05"


# --------------------------------------------------------------------------
# 各社プロバイダ
# --------------------------------------------------------------------------

class FakeHttp:
    """urlopen を差し替えて、送ったリクエストを記録する。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        payload = self.responses.pop(0)

        class Response:
            def read(self_inner):
                return json.dumps(payload).encode("utf-8")

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

        return Response()


def _install_http(monkeypatch, responses):
    fake = FakeHttp(responses)
    monkeypatch.setattr(providers.urllib.request, "urlopen", fake)
    return fake


def test_provider_of_and_bare_model():
    assert providers.provider_of("gpt-transcribe") == "openai"
    assert providers.provider_of("groq/whisper-large-v3-turbo") == "groq"
    assert providers.provider_of("gemini/gemini-3.1-flash-lite") == "gemini"
    assert providers.bare_model("gemini/gemini-3.1-flash-lite") == "gemini-3.1-flash-lite"
    assert providers.bare_model("gpt-transcribe") == "gpt-transcribe"
    # 未知の接頭辞はモデル名の一部として扱う
    assert providers.provider_of("my-model/v2") == "openai"


def test_gemini_request_and_response(tmp_path, monkeypatch):
    src = make_mp3(tmp_path / "a.mp3", 5)
    fake = _install_http(monkeypatch, [
        {"candidates": [{"content": {"parts": [{"text": "こんにちは"}]}}]}
    ])
    text = providers.transcribe_chunk(
        "gemini/gemini-3.1-flash-lite", src, "KEY", language="ja", keywords=["Claude"]
    )
    assert text == "こんにちは"

    request = fake.requests[0]
    assert "gemini-3.1-flash-lite:generateContent" in request.full_url
    assert request.get_header("X-goog-api-key") == "KEY"
    body = json.loads(request.data.decode("utf-8"))
    parts = body["contents"][0]["parts"]
    assert "文字起こし" in parts[0]["text"]
    assert "Claude" in parts[0]["text"]                 # 固有名詞を指示に載せる
    assert parts[1]["inline_data"]["mime_type"] == "audio/mpeg"
    assert base64.b64decode(parts[1]["inline_data"]["data"]) == src.read_bytes()


def test_gemini_empty_response_raises(tmp_path, monkeypatch):
    src = make_mp3(tmp_path / "a.mp3", 5)
    _install_http(monkeypatch, [{"promptFeedback": {"blockReason": "SAFETY"}}])
    with pytest.raises(providers.ProviderError):
        providers.transcribe_chunk("gemini/gemini-3.5-flash", src, "KEY")


def test_elevenlabs_multipart(tmp_path, monkeypatch):
    src = make_mp3(tmp_path / "b.mp3", 5)
    fake = _install_http(monkeypatch, [{"text": "hello world"}])
    assert providers.transcribe_chunk(
        "elevenlabs/scribe_v1", src, "KEY", language="ja"
    ) == "hello world"

    request = fake.requests[0]
    assert request.full_url.endswith("/v1/speech-to-text")
    assert request.get_header("Xi-api-key") == "KEY"
    assert "multipart/form-data" in request.get_header("Content-type")
    body = request.data
    assert b'name="model_id"' in body and b"scribe_v1" in body
    assert b'name="language_code"' in body
    assert b'filename="b.mp3"' in body
    assert src.read_bytes() in body


def test_deepgram_query_and_body(tmp_path, monkeypatch):
    src = make_mp3(tmp_path / "c.mp3", 5)
    fake = _install_http(monkeypatch, [
        {"results": {"channels": [{"alternatives": [{"transcript": "テスト"}]}]}}
    ])
    assert providers.transcribe_chunk(
        "deepgram/nova-3", src, "KEY", language="ja"
    ) == "テスト"

    request = fake.requests[0]
    assert "model=nova-3" in request.full_url and "language=ja" in request.full_url
    assert request.get_header("Authorization") == "Token KEY"
    assert request.data == src.read_bytes()          # 生の音声をそのまま送る


def test_assemblyai_upload_then_poll(tmp_path, monkeypatch):
    src = make_mp3(tmp_path / "d.mp3", 5)
    fake = _install_http(monkeypatch, [
        {"upload_url": "https://cdn.example/audio"},
        {"id": "job-1", "status": "queued"},
        {"status": "processing"},
        {"status": "completed", "text": "できました"},
    ])
    monkeypatch.setattr(providers.time, "sleep", lambda _s: None)
    assert providers.transcribe_chunk("assemblyai/best", src, "KEY") == "できました"

    urls = [r.full_url for r in fake.requests]
    assert urls[0].endswith("/v2/upload")
    assert urls[1].endswith("/v2/transcript")
    assert urls[2].endswith("/v2/transcript/job-1")
    created = json.loads(fake.requests[1].data.decode("utf-8"))
    assert created["audio_url"] == "https://cdn.example/audio"
    assert created["speech_model"] == "best"
    assert created["language_detection"] is True     # 言語未指定なら自動判定


def test_assemblyai_error_status(tmp_path, monkeypatch):
    src = make_mp3(tmp_path / "e.mp3", 5)
    _install_http(monkeypatch, [
        {"upload_url": "u"}, {"id": "x"}, {"status": "error", "error": "壊れた音声"},
    ])
    monkeypatch.setattr(providers.time, "sleep", lambda _s: None)
    with pytest.raises(providers.ProviderError, match="壊れた音声"):
        providers.transcribe_chunk("assemblyai/best", src, "KEY")


def test_http_error_surfaces_detail(tmp_path, monkeypatch):
    src = make_mp3(tmp_path / "f.mp3", 5)

    def raise_http(request, timeout=None):
        raise providers.urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":"bad key"}')
        )

    monkeypatch.setattr(providers.urllib.request, "urlopen", raise_http)
    with pytest.raises(providers.ProviderError, match="401"):
        providers.transcribe_chunk("deepgram/nova-3", src, "KEY")


def test_resolve_key_prefers_explicit_then_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    assert providers.resolve_key("gemini/gemini-3.5-flash", {"gemini": "explicit"}) == "explicit"
    assert providers.resolve_key("gemini/gemini-3.5-flash") == "env-key"
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setattr(providers, "PROVIDERS", dict(providers.PROVIDERS))
    assert "Google Gemini" in providers.missing_key_message("gemini/gemini-3.5-flash")


def test_transcribe_chunks_routes_to_other_provider(tmp_path, monkeypatch):
    calls = []

    def fake_chunk(model_id, path, api_key, **kwargs):
        calls.append((model_id, api_key, kwargs.get("context")))
        return f"断片{len(calls)}"

    monkeypatch.setattr(transcribe.providers, "transcribe_chunk", fake_chunk)
    result = transcribe.transcribe_chunks(
        None, _chunks(tmp_path, 2), model="gemini/gemini-3.1-flash-lite",
        api_keys={"gemini": "KEY"},
    )
    assert result.text == "断片1\n断片2"
    assert result.model == "gemini/gemini-3.1-flash-lite"
    assert calls[0][1] == "KEY"
    assert calls[0][2] is None            # 最初は文脈なし
    assert calls[1][2] == "断片1"          # 直前の結果を引き継ぐ


def test_transcribe_chunks_requires_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.setattr(providers, "resolve_key", lambda *a, **k: None)
    with pytest.raises(transcribe.TranscriptionError, match="Deepgram"):
        transcribe.transcribe_chunks(
            None, _chunks(tmp_path, 1), model="deepgram/nova-3"
        )


def test_groq_uses_openai_compatible_client(tmp_path, monkeypatch):
    fake = FakeTranscriptions()
    monkeypatch.setattr(
        transcribe, "_openai_compatible_client",
        lambda model, client, keys: FakeClient(fake),
    )
    result = transcribe.transcribe_chunks(
        None, _chunks(tmp_path, 1), model="groq/whisper-large-v3-turbo",
        api_keys={"groq": "KEY"},
    )
    assert result.text == "テキスト1"
    # プロバイダ接頭辞を外した名前で呼ぶ
    assert fake.calls[0]["model"] == "whisper-large-v3-turbo"


# --------------------------------------------------------------------------
# 料金
# --------------------------------------------------------------------------

FAKE_PRICE_JSON = {
    "gpt-transcribe": {"input_cost_per_second": 7.5e-05},
    "gpt-4o-transcribe": {"input_cost_per_audio_token": 2.5e-06},
    "gpt-4o-mini-transcribe": {"input_cost_per_audio_token": 1.25e-06},
    "whisper-1": {"input_cost_per_second": 0.0001},
    "gpt-5.6-sol": {"input_cost_per_token": 5e-06, "output_cost_per_token": 3e-05},
    "gpt-5.6-luna": {"input_cost_per_token": 2e-07, "output_cost_per_token": 1.2e-06},
    "gpt-5-nano": {"input_cost_per_token": 5e-08, "output_cost_per_token": 4e-07},
    "gpt-4.1-nano": {"input_cost_per_token": 1e-07, "output_cost_per_token": 4e-07},
    "無関係なモデル": {"input_cost_per_token": 1.0},
}


def _table(origin="live"):
    return pricing.PriceTable(prices=dict(FAKE_PRICE_JSON), origin=origin, fetched_at=0)


def test_fetch_prices_keeps_only_known_models(monkeypatch):
    class FakeResponse:
        def read(self):
            return json.dumps(FAKE_PRICE_JSON).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(pricing.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    prices = pricing.fetch_prices()
    assert "gpt-transcribe" in prices
    assert "無関係なモデル" not in prices     # 対象モデルだけ残す


def test_load_prices_falls_back_to_builtin(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise OSError("ネットワークに接続できません")

    monkeypatch.setattr(pricing, "fetch_prices", boom)
    monkeypatch.setattr(pricing, "CACHE_PATH", tmp_path / "prices.json")
    table = pricing.load_prices()
    assert table.origin == "builtin"
    assert "参考価格" in table.origin_label()
    assert table.transcribe_cost_per_minute("gpt-transcribe") is not None


def test_load_prices_uses_cache_when_offline(monkeypatch, tmp_path):
    cache = tmp_path / "prices.json"
    cache.write_text(
        json.dumps({"fetched_at": 0, "prices": FAKE_PRICE_JSON}), encoding="utf-8"
    )
    monkeypatch.setattr(pricing, "CACHE_PATH", cache)
    table = pricing.load_prices(offline=True)
    assert table.origin == "cache"
    assert "前回取得した価格" in table.origin_label()


def test_price_conversions():
    table = _table()
    # 1 秒あたり課金 → 1 分あたり
    assert table.transcribe_cost_per_minute("gpt-transcribe") == pytest.approx(0.0045)
    # 音声トークン課金 → 1 分あたり
    assert table.transcribe_cost_per_minute("gpt-4o-transcribe") == pytest.approx(0.006)
    assert table.polish_cost_per_mtok("gpt-5.6-sol") == pytest.approx((5.0, 30.0))
    assert table.transcribe_cost_per_minute("存在しないモデル") is None


def test_cheapest_models_are_sorted_by_price():
    table = _table()
    cheap = pricing.cheapest_models(table, "transcribe", 3)
    prices = [table.transcribe_cost_per_minute(m.name) for m in cheap]
    assert prices == sorted(prices)
    assert cheap[0].name == "groq/whisper-large-v3-turbo"   # 現時点の最安

    cheap_polish = [m.name for m in pricing.cheapest_models(table, "polish", 3)]
    assert cheap_polish[0] == "gpt-5-nano"
    assert "gpt-5.6-sol" not in cheap_polish        # 最高品質＝最安ではない


def test_transcribe_choices_are_six_across_providers():
    table = _table()
    models = pricing.selectable_models(table, "transcribe", "gpt-transcribe")
    names = [m.name for m in models]

    assert len(names) == pricing.TRANSCRIBE_CHOICES == 6
    assert names[0] == "gpt-transcribe"                     # 既定（最高精度）は先頭
    assert "groq/whisper-large-v3-turbo" in names           # 最安は必ず入る
    assert any(m.provider == "gemini" for m in models)      # 他社モデルも並ぶ
    assert len(names) == len(set(names))
    # 1 社に偏らない（6 枠に 6 社）
    assert len({m.provider for m in models}) == 6


def test_selectable_models_include_default_and_cheap():
    table = _table()
    names = [m.name for m in pricing.selectable_models(table, "polish", "gpt-5.6-sol")]
    assert names[0] == "gpt-5.6-sol"               # 既定は先頭
    assert "gpt-5-nano" in names                   # 安いものも選べる
    assert len(names) == len(set(names))           # 重複しない

    # 既定が最安でもある場合は重複させない
    names = [m.name for m in pricing.selectable_models(table, "polish", "gpt-5-nano")]
    assert names.count("gpt-5-nano") == 1


def test_choice_roundtrip():
    table = _table()
    model = pricing.TRANSCRIBE_MODELS[0]
    choice = pricing.format_choice(table, model)
    assert "$" in choice
    assert pricing.model_from_choice(choice) == model.name


def test_estimate_costs():
    table = _table()
    # 60 分 × $0.0045/分
    assert table.estimate_transcribe("gpt-transcribe", 60) == pytest.approx(0.27)
    cheap = table.estimate_polish("gpt-5-nano", minutes=60)
    expensive = table.estimate_polish("gpt-5.6-sol", minutes=60)
    assert cheap < expensive
    text = pricing.format_estimate(table, "gpt-transcribe", "gpt-5.6-sol", 60)
    assert "概算費用" in text and "文字起こし" in text and "推敲" in text
    assert "推敲" not in pricing.format_estimate(table, "gpt-transcribe", None, 60)
    assert "不明" in pricing.format_estimate(table, "gpt-transcribe", None, None)


def test_price_report_lists_both_kinds():
    report = pricing.price_report(_table())
    assert "文字起こしモデル" in report
    assert "推敲モデル" in report
    assert "現時点で安い順" in report


def test_cli_list_models(monkeypatch, capsys):
    from audio_transcriber.cli import main

    monkeypatch.setattr(pricing, "load_prices", lambda **kwargs: _table())
    assert main(["--list-models"]) == 0
    assert "文字起こしモデル" in capsys.readouterr().out


def test_cli_parser_defaults():
    from audio_transcriber.cli import build_parser

    args = build_parser().parse_args(["a.mp3", "b.wav", "--language", "ja"])
    assert args.inputs == ["a.mp3", "b.wav"]
    assert args.model == transcribe.DEFAULT_TRANSCRIBE_MODEL
    assert args.polish_model == polish.DEFAULT_POLISH_MODEL
    assert args.style == "readable"
    assert args.no_polish is False
