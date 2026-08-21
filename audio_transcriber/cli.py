"""コマンドラインインターフェース。

    python -m audio_transcriber 会議.mp3
    python -m audio_transcriber *.wav -o out --language ja --style article
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import audio, polish as polish_mod, pricing, providers, transcribe as transcribe_mod
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
            "  audio-transcribe --list-models                 # 最新料金でモデルを比較\n"
            "  audio-transcribe 録音.wav -o 出力 --language ja --style article\n"
            "  audio-transcribe 長時間.mp3 --model gpt-4o-mini-transcribe --polish-model gpt-5-nano\n"
        ),
    )
    p.add_argument("inputs", nargs="*", help="音声ファイル（mp3 / wav / m4a など）")
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
    p.add_argument(
        "--list-models",
        action="store_true",
        help="選べるモデルと最新の料金を表示して終了する",
    )
    p.add_argument(
        "--offline-prices",
        action="store_true",
        help="料金の取得をスキップする（キャッシュまたは参考価格を使う）",
    )
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # 起動のたびに最新の料金を取り、どのモデルが安いか分かるようにする
    prices = pricing.load_prices(offline=args.offline_prices)
    if args.list_models:
        print(pricing.price_report(prices))
        return 0
    if not args.inputs:
        parser.error("音声ファイルを指定してください（一覧は --list-models）")

    print(prices.origin_label(), file=sys.stderr)
    transcribe_info = pricing.ModelInfo(args.model, "transcribe", "")
    print(
        f"文字起こし: {args.model}（{transcribe_info.provider_label} / "
        f"{pricing.format_price(prices, transcribe_info)}）",
        file=sys.stderr,
    )
    if not args.no_polish:
        print(
            f"推敲　　　: {args.polish_model}"
            f"（{pricing.format_price(prices, pricing.ModelInfo(args.polish_model, 'polish', ''))}）",
            file=sys.stderr,
        )

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

    # OpenAI のキーは「OpenAI で文字起こしする」か「推敲する」ときだけ必要
    client = None
    if options.do_polish or providers.provider_of(args.model) == providers.DEFAULT_PROVIDER:
        try:
            client = make_client(args.api_key)
        except TranscriberError as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            return 2
    if providers.resolve_key(args.model) is None:
        print(f"エラー: {providers.missing_key_message(args.model)}", file=sys.stderr)
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
        print(
            pricing.format_estimate(
                prices,
                result.transcribe_model,
                result.polish_model,
                minutes=(result.duration / 60) if result.duration else None,
                chars=len(result.transcript),
            ),
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
