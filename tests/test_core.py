"""図形検出と PPTX 変換のテスト。合成画像/PPTX を生成して検証する。"""

import io

import cv2
import numpy as np
import pytest
from pptx import Presentation
from pptx.util import Inches

from pptx_vectorizer.core import (
    convert_presentation,
    detect_shapes_in_image,
)


def _make_image_with_shapes() -> bytes:
    """白背景に矩形と円を描いた PNG バイト列を返す。"""
    img = np.full((400, 400, 3), 255, dtype=np.uint8)
    cv2.rectangle(img, (40, 40), (180, 180), (0, 0, 255), -1)   # 赤い矩形
    cv2.circle(img, (300, 300), 70, (255, 0, 0), -1)            # 青い円
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_detect_shapes_finds_rectangle_and_ellipse():
    shapes = detect_shapes_in_image(_make_image_with_shapes())
    kinds = {s.kind for s in shapes}
    assert "rectangle" in kinds
    assert "ellipse" in kinds


def test_detect_empty_for_blank_image():
    img = np.full((200, 200, 3), 255, dtype=np.uint8)
    _, buf = cv2.imencode(".png", img)
    assert detect_shapes_in_image(buf.tobytes()) == []


def test_detect_invalid_bytes_returns_empty():
    assert detect_shapes_in_image(b"not an image") == []


def _make_pptx_with_picture(path: str) -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # 空白レイアウト
    slide.shapes.add_picture(
        io.BytesIO(_make_image_with_shapes()),
        Inches(1), Inches(1), Inches(4), Inches(4),
    )
    prs.save(path)


def test_convert_presentation_creates_shapes(tmp_path):
    src = tmp_path / "in.pptx"
    dst = tmp_path / "out.pptx"
    _make_pptx_with_picture(str(src))

    report = convert_presentation(str(src), str(dst))

    assert report.pictures_processed == 1
    assert report.shapes_created >= 2
    assert dst.exists()

    # 出力にオートシェイプが含まれることを確認
    out = Presentation(str(dst))
    names = [s.name for s in out.slides[0].shapes]
    assert any(n.startswith("Vectorized-") for n in names)


def test_convert_remove_original(tmp_path):
    src = tmp_path / "in.pptx"
    dst = tmp_path / "out.pptx"
    _make_pptx_with_picture(str(src))

    convert_presentation(str(src), str(dst), remove_original_picture=True)

    out = Presentation(str(dst))
    pic_count = sum(1 for s in out.slides[0].shapes if s.shape_type == 13)
    assert pic_count == 0
