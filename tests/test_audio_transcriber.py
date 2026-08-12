"""音声分割・文字起こし・推敲のテスト。

API は呼ばず、合成した音声ファイルと偽クライアントで検証する。
"""

import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from audio_transcriber import audio, core, polish, transcribe

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


def test_cli_parser_defaults():
    from audio_transcriber.cli import build_parser

    args = build_parser().parse_args(["a.mp3", "b.wav", "--language", "ja"])
    assert args.inputs == ["a.mp3", "b.wav"]
    assert args.model == transcribe.DEFAULT_TRANSCRIBE_MODEL
    assert args.polish_model == polish.DEFAULT_POLISH_MODEL
    assert args.style == "readable"
    assert args.no_polish is False
