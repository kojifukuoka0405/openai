"""コマンドラインインターフェース。

    python -m audio_transcriber 会議.mp3
    python -m audio_transcriber *.wav -o out --language ja --style article
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import audio, polish as polish_mod, transcribe as transcribe_mod
from .core import (
    Options,
    TranscriberError,
    format_duration,
    make_client,
    transcribe_and_polish,
    write_outputs,
)


def _progress(done: int, total: int, message: str) -> None:
    print(f"[{done:3d}%] {message}", file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="audio-transcribe",
        description=(
            "音声ファイルを高精度モデルで文字起こしし、推敲した版も出力します。"
            "（MP3 / WAV ほか対応）"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例:\n"
            "  audio-transcribe 会議.mp3\n"
            "  audio-transcribe 録音.wav -o 出力 --language ja --style article\n"
            "  audio-transcribe *.mp3 --keywords 'Anthropic,Claude,PoC'\n"
        ),
    )
    p.add_argument("inputs", nargs="+", help="音声ファイル（mp3 / wav / m4a など）")
    p.add_argument("-o", "--outdir", help="出力先ディレクトリ（既定: 入力と同じ場所）")
    p.add_argument(
        "--model",
        default=transcribe_mod.DEFAULT_TRANSCRIBE_MODEL,
        help=f"文字起こしモデル（既定: {transcribe_mod.DEFAULT_TRANSCRIBE_MODEL}）",
    )
    p.add_argument(
        "--polish-model",
        default=polish_mod.DEFAULT_POLISH_MODEL,
        help=f"推敲に使うモデル（既定: {polish_mod.DEFAULT_POLISH_MODEL}）",
    )
    p.add_argument("--language", help="音声の言語コード（例: ja, en）。未指定なら自動判定")
    p.add_argument(
        "--keywords",
        help="固有名詞・専門用語をカンマ区切りで指定（認識精度が上がる）",
    )
    p.add_argument(
        "--style",
        choices=sorted(polish_mod.STYLE_PRESETS),
        default="readable",
        help="推敲の方針（readable: 読みやすい書き言葉 / verbatim: 最小限 / article: 記事風）",
    )
    p.add_argument("--instructions", help="推敲への追加指示（自由記述）")
    p.add_argument("--no-polish", action="store_true", help="推敲版を作らない")
    p.add_argument(
        "--max-seconds",
        type=float,
        default=audio.DEFAULT_MAX_SECONDS,
        help=f"1 リクエストあたりの最大音声長（秒、既定 {int(audio.DEFAULT_MAX_SECONDS)}）",
    )
    p.add_argument("--api-key", help="OpenAI API キー（既定: 環境変数 OPENAI_API_KEY）")
    p.add_argument("--print", dest="print_text", action="store_true", help="結果を標準出力にも表示")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    options = Options(
        transcribe_model=args.model,
        polish_model=args.polish_model,
        language=args.language,
        keywords=tuple(k.strip() for k in (args.keywords or "").split(",") if k.strip()),
        style=args.style,
        extra_instructions=args.instructions,
        do_polish=not args.no_polish,
        max_seconds=args.max_seconds,
    )

    try:
        client = make_client(args.api_key)
    except TranscriberError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2

    failures = 0
    for raw in args.inputs:
        path = Path(raw)
        print(f"\n=== {path.name} ===", file=sys.stderr)
        try:
            result = transcribe_and_polish(
                path, options, client=client, progress=_progress
            )
        except (TranscriberError, audio.AudioError, transcribe_mod.TranscriptionError,
                polish_mod.PolishError) as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            failures += 1
            continue

        raw_path, polished_path = write_outputs(result, args.outdir)
        print(
            f"モデル: {result.transcribe_model}"
            + (f" / 推敲: {result.polish_model}" if result.polish_model else "")
            + f" / 長さ: {format_duration(result.duration)}"
            + f" / 分割: {result.chunk_count}",
            file=sys.stderr,
        )
        print(f"文字起こし: {raw_path}", file=sys.stderr)
        if polished_path:
            print(f"推敲版　　: {polished_path}", file=sys.stderr)
        if args.print_text:
            print("\n--- 文字起こし ---\n" + result.transcript)
            if result.polished:
                print("\n--- 推敲版 ---\n" + result.polished)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
