"""音声ファイルの検査と分割。

OpenAI の音声 API には 1 リクエストあたり 25MB の上限があるため、長い録音は
分割して送る必要がある。ここでは **外部ツールなし**（Python 標準ライブラリのみ）で
MP3 と WAV を安全に分割できるようにしている。

- WAV: `wave` モジュールでフレーム単位に分割（16bit PCM なら、境界を
  「一番静かなところ」に寄せて単語の途中で切れにくくする）
- MP3: MPEG オーディオフレームのヘッダを解析し、**フレーム境界**で分割
  （バイト数で雑に切るとノイズや復号エラーになるため）
- その他の形式（m4a/mp4/ogg/flac/webm …）: 上限以内ならそのまま送信。
  上限を超える場合は ffmpeg が必要。

ffmpeg が入っている場合は、まず 16kHz モノラル MP3 へ変換してから分割する。
音声認識に必要な情報を保ったままサイズが 1/10 以下になるので、多くの録音が
「分割なし = 継ぎ目なし」で 1 回の送信に収まる（＝精度が上がる）。
"""

from __future__ import annotations

import array
import shutil
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

# OpenAI の音声エンドポイントが受け付けるファイル形式
SUPPORTED_EXTENSIONS = {
    ".flac", ".m4a", ".mp3", ".mp4", ".mpeg", ".mpga",
    ".oga", ".ogg", ".wav", ".webm",
}

# API の上限は 25MB。マージンを取って 24MB を分割の目安にする
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
SAFE_CHUNK_BYTES = 24 * 1024 * 1024

# 1 リクエストあたりの音声長の目安（秒）。長すぎるとタイムアウトしやすい
DEFAULT_MAX_SECONDS = 900.0  # 15 分

# ffmpeg で圧縮するときの設定（音声認識には十分な品質）
FFMPEG_SAMPLE_RATE = 16000
FFMPEG_BITRATE = "64k"

ProgressFn = Callable[[str], None]


class AudioError(RuntimeError):
    """音声ファイルを扱えなかったときのエラー。"""


@dataclass
class AudioChunk:
    """送信単位となる音声の断片。"""

    path: Path
    index: int
    start: float                    # 元ファイル先頭からの位置（秒）
    duration: Optional[float]       # 断片の長さ（秒、不明なら None）
    temporary: bool = False         # 分割で作った一時ファイルか


# --------------------------------------------------------------------------
# ffmpeg（あれば使う）
# --------------------------------------------------------------------------

def ffmpeg_path() -> Optional[str]:
    """ffmpeg の実行ファイルパス。無ければ None。"""
    return shutil.which("ffmpeg")


def transcode_to_mp3(src: Path, dest: Path) -> Path:
    """ffmpeg で 16kHz モノラル MP3 に変換する（サイズを大幅に削減）。"""
    exe = ffmpeg_path()
    if exe is None:
        raise AudioError("ffmpeg が見つかりません。")
    cmd = [
        exe, "-y", "-loglevel", "error", "-i", str(src),
        "-vn", "-ac", "1", "-ar", str(FFMPEG_SAMPLE_RATE),
        "-c:a", "libmp3lame", "-b:a", FFMPEG_BITRATE, str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:  # pragma: no cover - 環境依存
        detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
        raise AudioError(f"ffmpeg での変換に失敗しました: {detail}") from exc
    if not dest.exists() or dest.stat().st_size == 0:  # pragma: no cover
        raise AudioError("ffmpeg での変換に失敗しました（出力が空です）。")
    return dest


# --------------------------------------------------------------------------
# MP3: MPEG オーディオフレームの解析
# --------------------------------------------------------------------------

# ビットレート表（kbps）。キーは (MPEG バージョン, レイヤー)
_BITRATE_TABLE = {
    (1, 1): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
    (1, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
    (1, 3): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    (2, 1): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
    (2, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
_BITRATE_TABLE[(2, 3)] = _BITRATE_TABLE[(2, 2)]

# サンプリング周波数（Hz）。キーはバージョン ID ビット
_SAMPLE_RATES = {
    3: [44100, 48000, 32000],   # MPEG1
    2: [22050, 24000, 16000],   # MPEG2
    0: [11025, 12000, 8000],    # MPEG2.5
}

# 1 フレームのサンプル数。キーは (MPEG バージョン, レイヤー)
_SAMPLES_PER_FRAME = {
    (1, 1): 384, (1, 2): 1152, (1, 3): 1152,
    (2, 1): 384, (2, 2): 1152, (2, 3): 576,
}


@dataclass
class Mp3Frame:
    offset: int
    length: int
    duration: float


def _syncsafe(raw: bytes) -> int:
    return (raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]


def skip_id3v2(data: bytes, pos: int = 0) -> int:
    """先頭の ID3v2 タグを読み飛ばした位置を返す。"""
    while data[pos : pos + 3] == b"ID3" and len(data) >= pos + 10:
        flags = data[pos + 5]
        size = _syncsafe(data[pos + 6 : pos + 10])
        pos += 10 + size
        if flags & 0x10:      # フッタ付き
            pos += 10
    return pos


def parse_mp3_frame(data: bytes, pos: int) -> Optional[Mp3Frame]:
    """pos にある MPEG オーディオフレームを解析する。無効なら None。"""
    if pos + 4 > len(data):
        return None
    h = data[pos : pos + 4]
    if h[0] != 0xFF or (h[1] & 0xE0) != 0xE0:
        return None

    version_bits = (h[1] >> 3) & 0x03
    layer_bits = (h[1] >> 1) & 0x03
    bitrate_index = (h[2] >> 4) & 0x0F
    rate_index = (h[2] >> 2) & 0x03
    padding = (h[2] >> 1) & 0x01

    if version_bits == 1 or layer_bits == 0:      # 予約値
        return None
    if bitrate_index in (0, 0x0F) or rate_index == 3:  # free/bad
        return None

    version = 1 if version_bits == 3 else 2       # MPEG2.5 は MPEG2 と同じ表
    layer = 4 - layer_bits                        # 01->3, 10->2, 11->1
    bitrate = _BITRATE_TABLE[(version, layer)][bitrate_index] * 1000
    sample_rate = _SAMPLE_RATES[version_bits][rate_index]
    samples = _SAMPLES_PER_FRAME[(version, layer)]

    if layer == 1:
        length = (12 * bitrate // sample_rate + padding) * 4
    else:
        length = (samples // 8) * bitrate // sample_rate + padding

    if length <= 4 or pos + length > len(data):
        return None
    return Mp3Frame(offset=pos, length=length, duration=samples / sample_rate)


def iter_mp3_frames(data: bytes) -> List[Mp3Frame]:
    """MP3 データからフレーム一覧を取り出す（ID3 タグや壊れた部分は読み飛ばす）。"""
    frames: List[Mp3Frame] = []
    pos = skip_id3v2(data)
    size = len(data)
    while pos < size:
        if data[pos : pos + 3] == b"TAG" and size - pos == 128:
            break                                  # 末尾の ID3v1
        frame = parse_mp3_frame(data, pos)
        if frame is None:
            nxt = data.find(b"\xff", pos + 1)      # 同期はずれ → 次の 0xFF へ
            if nxt < 0:
                break
            pos = nxt
            continue
        frames.append(frame)
        pos += frame.length
    return frames


def _is_vbr_header(data: bytes, frame: Mp3Frame) -> bool:
    """Xing/Info/VBRI ヘッダだけのフレームか（音声データを含まない）。"""
    blob = data[frame.offset : frame.offset + frame.length]
    return b"Xing" in blob or b"Info" in blob or b"VBRI" in blob


def mp3_duration(path: Path) -> Optional[float]:
    """MP3 の再生時間（秒）。解析できなければ None。"""
    data = path.read_bytes()
    frames = iter_mp3_frames(data)
    if not frames:
        return None
    return sum(f.duration for f in frames)


def split_mp3(
    src: Path,
    outdir: Path,
    max_bytes: int = SAFE_CHUNK_BYTES,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> List[AudioChunk]:
    """MP3 をフレーム境界で分割する。"""
    outdir.mkdir(parents=True, exist_ok=True)
    data = src.read_bytes()
    frames = iter_mp3_frames(data)
    if not frames:
        raise AudioError(f"MP3 として解析できませんでした: {src.name}")
    if frames and _is_vbr_header(data, frames[0]):
        frames = frames[1:]                        # VBR ヘッダは捨てる

    chunks: List[AudioChunk] = []
    start_time = 0.0
    cur: List[Mp3Frame] = []
    cur_bytes = 0
    cur_seconds = 0.0

    def flush() -> None:
        nonlocal cur, cur_bytes, cur_seconds, start_time
        if not cur:
            return
        index = len(chunks)
        dest = outdir / f"{src.stem}_part{index + 1:03d}.mp3"
        with open(dest, "wb") as fh:
            for f in cur:
                fh.write(data[f.offset : f.offset + f.length])
        chunks.append(AudioChunk(dest, index, start_time, cur_seconds, temporary=True))
        start_time += cur_seconds
        cur, cur_bytes, cur_seconds = [], 0, 0.0

    for frame in frames:
        too_big = cur and (cur_bytes + frame.length > max_bytes)
        too_long = cur and (cur_seconds + frame.duration > max_seconds)
        if too_big or too_long:
            flush()
        cur.append(frame)
        cur_bytes += frame.length
        cur_seconds += frame.duration
    flush()
    return chunks


# --------------------------------------------------------------------------
# WAV
# --------------------------------------------------------------------------

def wav_duration(path: Path) -> Optional[float]:
    """WAV の再生時間（秒）。読めなければ None。"""
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            return wf.getnframes() / rate if rate else None
    except (wave.Error, EOFError):
        return None


def _quiet_cut_point(frames: bytes, channels: int, sampwidth: int, target: int, search: int) -> int:
    """target の手前（最大 search フレーム）で一番静かな位置を探して返す。

    16bit PCM のみ対象。単語の途中でぶつ切りになるのを減らすための調整。
    サイズ・長さの上限を超えないよう、**手前方向にだけ**動かす。
    見つからなければ target をそのまま返す。
    """
    if sampwidth != 2 or search <= 0:
        return target
    lo = max(0, target - search)
    hi = min(len(frames) // (channels * sampwidth), target)
    if hi - lo < 2:
        return target

    samples = array.array("h")
    samples.frombytes(frames[lo * channels * 2 : hi * channels * 2])
    if sys.byteorder == "big":  # pragma: no cover - WAV は常にリトルエンディアン
        samples.byteswap()

    window = max(1, (hi - lo) // 64)       # 探索範囲を 64 分割して比較
    best_pos, best_energy = target, None
    for w in range(0, (hi - lo) - window, window):
        seg = samples[w * channels : (w + window) * channels]
        energy = sum(abs(int(v)) for v in seg) / max(1, len(seg))
        if best_energy is None or energy < best_energy:
            best_energy = energy
            best_pos = lo + w + window // 2
    return best_pos


def split_wav(
    src: Path,
    outdir: Path,
    max_bytes: int = SAFE_CHUNK_BYTES,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> List[AudioChunk]:
    """WAV をフレーム単位で分割する（無音寄りの位置で切る）。"""
    outdir.mkdir(parents=True, exist_ok=True)
    chunks: List[AudioChunk] = []
    try:
        with wave.open(str(src), "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            rate = wf.getframerate()
            total = wf.getnframes()
            if rate <= 0 or channels <= 0 or sampwidth <= 0:
                raise AudioError(f"WAV のヘッダが不正です: {src.name}")

            frame_size = channels * sampwidth
            by_bytes = max(1, (max_bytes - 64) // frame_size)   # 64 は WAV ヘッダ分
            by_time = max(1, int(max_seconds * rate))
            step = min(by_bytes, by_time)
            search = min(step // 4, int(rate * 3))              # 探索幅（最大 3 秒）

            pos = 0
            while pos < total:
                # 1 断片ずつ読む（巨大な WAV でもメモリを使いすぎない）
                want = min(step, total - pos)
                wf.setpos(pos)
                data = wf.readframes(want)
                got = len(data) // frame_size
                if got == 0:
                    break
                if pos + got < total:
                    cut = _quiet_cut_point(data, channels, sampwidth, got, search)
                    cut = max(1, min(got, cut))
                    if cut < got:
                        data = data[: cut * frame_size]
                        got = cut

                index = len(chunks)
                dest = outdir / f"{src.stem}_part{index + 1:03d}.wav"
                with wave.open(str(dest), "wb") as out:
                    out.setnchannels(channels)
                    out.setsampwidth(sampwidth)
                    out.setframerate(rate)
                    out.writeframes(data)
                chunks.append(
                    AudioChunk(dest, index, pos / rate, got / rate, temporary=True)
                )
                pos += got
    except (wave.Error, EOFError) as exc:
        raise AudioError(
            f"WAV を読み込めませんでした（非圧縮 PCM のみ対応）: {src.name}"
        ) from exc
    return chunks


# --------------------------------------------------------------------------
# 共通
# --------------------------------------------------------------------------

def probe_duration(path: Path) -> Optional[float]:
    """再生時間（秒）を推定する。分からなければ None。"""
    ext = path.suffix.lower()
    if ext == ".wav":
        return wav_duration(path)
    if ext in (".mp3", ".mpga", ".mpeg"):
        try:
            return mp3_duration(path)
        except OSError:  # pragma: no cover
            return None
    exe = shutil.which("ffprobe")
    if exe is None:
        return None
    try:  # pragma: no cover - 環境依存
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        return float(out)
    except (subprocess.CalledProcessError, ValueError):  # pragma: no cover
        return None


def check_supported(path: Path) -> None:
    """拡張子が対応形式かを確認する。"""
    if not path.exists():
        raise AudioError(f"ファイルが見つかりません: {path}")
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        supported = " ".join(sorted(SUPPORTED_EXTENSIONS))
        raise AudioError(
            f"対応していない形式です: {ext or '(拡張子なし)'}\n対応形式: {supported}"
        )


def prepare_chunks(
    path: Path,
    workdir: Path,
    max_bytes: int = SAFE_CHUNK_BYTES,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    progress: Optional[ProgressFn] = None,
    allow_ffmpeg: bool = True,
) -> List[AudioChunk]:
    """送信できるサイズの音声断片リストを返す。

    そのまま送れる場合は、元ファイル 1 個だけのリストを返す（＝継ぎ目なし）。
    """
    path = Path(path)
    check_supported(path)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    def notify(msg: str) -> None:
        if progress is not None:
            progress(msg)

    size = path.stat().st_size
    duration = probe_duration(path)
    fits = size <= max_bytes and (duration is None or duration <= max_seconds)
    if fits:
        return [AudioChunk(path, 0, 0.0, duration)]

    source = path
    ext = path.suffix.lower()

    # ffmpeg があれば、まず圧縮して 1 回で送れないか試す
    if allow_ffmpeg and ffmpeg_path() is not None:
        notify("音声を圧縮しています（16kHz モノラル MP3）…")
        compressed = workdir / f"{path.stem}_16k.mp3"
        transcode_to_mp3(path, compressed)
        comp_duration = duration if duration is not None else mp3_duration(compressed)
        if compressed.stat().st_size <= max_bytes and (
            comp_duration is None or comp_duration <= max_seconds
        ):
            return [AudioChunk(compressed, 0, 0.0, comp_duration, temporary=True)]
        source = compressed
        ext = ".mp3"

    notify("音声を分割しています…")
    if ext in (".mp3", ".mpga", ".mpeg"):
        return split_mp3(source, workdir, max_bytes, max_seconds)
    if ext == ".wav":
        return split_wav(source, workdir, max_bytes, max_seconds)

    raise AudioError(
        f"{ext} の大きなファイルを分割するには ffmpeg が必要です。\n"
        "ffmpeg をインストールするか、MP3 / WAV に変換してからお試しください。\n"
        "  - Windows: winget install Gyan.FFmpeg\n"
        "  - macOS:   brew install ffmpeg\n"
        "  - Ubuntu:  sudo apt-get install ffmpeg"
    )
