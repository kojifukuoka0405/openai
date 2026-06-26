"""pptx_vectorizer: PPT 内の画像を編集可能な図形オブジェクトに変換するツール。

画像（埋め込みピクチャ）から矩形・円・三角形などの単純な図形を検出し、
PowerPoint のオートシェイプ（編集可能オブジェクト）として同じ位置に配置する。
"""

from .core import (
    convert_presentation,
    DetectedShape,
    detect_shapes_in_image,
    add_editable_rectangle,
    representative_color,
)

__all__ = [
    "convert_presentation",
    "DetectedShape",
    "detect_shapes_in_image",
    "add_editable_rectangle",
    "representative_color",
]
__version__ = "0.1.0"
