"""コマンドラインインターフェース。"""

from __future__ import annotations

import argparse
import sys

from .core import convert_presentation


def _progress(done: int, total: int, message: str) -> None:
    print(f"[{done}/{total}] {message}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pptx-vectorize",
        description="PPT 内の画像から図形を検出し、編集可能なオートシェイプに変換します。",
    )
    p.add_argument("input", help="入力 .pptx ファイル")
    p.add_argument("output", help="出力 .pptx ファイル")
    p.add_argument(
        "--remove-original",
        action="store_true",
        help="変換後、元の画像を削除する（既定は残す）",
    )
    p.add_argument(
        "--min-area-ratio",
        type=float,
        default=0.002,
        help="検出する図形の最小面積比（既定 0.002）",
    )
    p.add_argument(
        "--max-area-ratio",
        type=float,
        default=0.98,
        help="検出する図形の最大面積比（既定 0.98）",
    )
    p.add_argument(
        "--epsilon",
        type=float,
        default=0.03,
        help="輪郭近似の許容誤差（既定 0.03）",
    )
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = convert_presentation(
            args.input,
            args.output,
            remove_original_picture=args.remove_original,
            progress=_progress,
            min_area_ratio=args.min_area_ratio,
            max_area_ratio=args.max_area_ratio,
            approx_epsilon=args.epsilon,
        )
    except FileNotFoundError:
        print(f"エラー: 入力ファイルが見つかりません: {args.input}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"エラー: 変換に失敗しました: {exc}", file=sys.stderr)
        return 1

    print(
        f"完了: 画像 {report.pictures_processed} 枚を処理し、"
        f"図形 {report.shapes_created} 個を生成しました -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
