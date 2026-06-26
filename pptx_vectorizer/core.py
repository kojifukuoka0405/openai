"""画像内の図形検出と PPTX への変換ロジック。"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.dml.color import RGBColor
from pptx.util import Emu, Pt


# OpenCV の近似頂点数 -> PowerPoint オートシェイプ種別の対応
SHAPE_BY_VERTICES = {
    3: MSO_SHAPE.ISOSCELES_TRIANGLE,
    4: MSO_SHAPE.RECTANGLE,
    5: MSO_SHAPE.REGULAR_PENTAGON,
    6: MSO_SHAPE.HEXAGON,
}


@dataclass
class DetectedShape:
    """画像内で検出された 1 つの図形（ピクセル座標系）。"""

    kind: str                       # "rectangle" | "ellipse" | "triangle" | ...
    bbox: Tuple[int, int, int, int]  # x, y, w, h（ピクセル）
    fill: Tuple[int, int, int]      # RGB
    line: Tuple[int, int, int]      # RGB（輪郭色）
    vertices: int = 0

    @property
    def area(self) -> int:
        _, _, w, h = self.bbox
        return w * h


def _dominant_color(image: np.ndarray, mask: np.ndarray) -> Tuple[int, int, int]:
    """マスク領域の平均色を RGB で返す（image は BGR）。"""
    pixels = image[mask.astype(bool)]
    if pixels.size == 0:
        return (255, 255, 255)
    b, g, r = pixels.mean(axis=0)
    return (int(r), int(g), int(b))


def detect_shapes_in_image(
    image_bytes: bytes,
    *,
    min_area_ratio: float = 0.002,
    max_area_ratio: float = 0.98,
    approx_epsilon: float = 0.03,
) -> List[DetectedShape]:
    """画像バイト列から単純図形を検出する。

    Args:
        image_bytes: PNG/JPEG などの画像バイト列。
        min_area_ratio: 画像全体に対する図形の最小面積比（ノイズ除去）。
        max_area_ratio: 最大面積比（画像枠そのものを拾わないため）。
        approx_epsilon: 輪郭近似の許容誤差（周長に対する比）。

    Returns:
        検出した DetectedShape のリスト（面積降順）。
    """
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        return []

    h, w = image.shape[:2]
    total_area = float(h * w)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    # 図形の輪郭を閉じるために膨張
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    results: List[DetectedShape] = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < total_area * min_area_ratio or area > total_area * max_area_ratio:
            continue

        peri = cv2.arcLength(cnt, True)
        if peri == 0:
            continue
        approx = cv2.approxPolyDP(cnt, approx_epsilon * peri, True)
        verts = len(approx)
        x, y, bw, bh = cv2.boundingRect(approx)

        # 円形度で楕円/円を判定
        circularity = 4 * math.pi * area / (peri * peri)
        if verts > 6 and circularity > 0.7:
            kind = "ellipse"
        elif verts == 4:
            kind = "rectangle"
        elif verts == 3:
            kind = "triangle"
        elif verts in SHAPE_BY_VERTICES:
            kind = {5: "pentagon", 6: "hexagon"}[verts]
        else:
            # 不明な多角形はバウンディングボックス矩形として扱う
            kind = "rectangle"
            verts = 4

        mask = np.zeros(gray.shape, dtype=np.uint8)
        cv2.drawContours(mask, [cnt], -1, 255, thickness=-1)
        fill = _dominant_color(image, mask)

        outline_mask = np.zeros(gray.shape, dtype=np.uint8)
        cv2.drawContours(outline_mask, [cnt], -1, 255, thickness=2)
        line = _dominant_color(image, outline_mask)

        results.append(
            DetectedShape(
                kind=kind,
                bbox=(x, y, bw, bh),
                fill=fill,
                line=line,
                vertices=verts,
            )
        )

    results.sort(key=lambda s: s.area, reverse=True)
    return results


_PPTX_SHAPE_ENUM = {
    "rectangle": MSO_SHAPE.RECTANGLE,
    "ellipse": MSO_SHAPE.OVAL,
    "triangle": MSO_SHAPE.ISOSCELES_TRIANGLE,
    "pentagon": MSO_SHAPE.REGULAR_PENTAGON,
    "hexagon": MSO_SHAPE.HEXAGON,
}


@dataclass
class ConversionReport:
    """変換結果のサマリ。"""

    pictures_processed: int = 0
    shapes_created: int = 0
    per_slide: List[int] = field(default_factory=list)


def _add_shape_for_detection(
    slide_shapes,
    det: DetectedShape,
    pic_left: int,
    pic_top: int,
    pic_width: int,
    pic_height: int,
    img_w: int,
    img_h: int,
) -> None:
    """検出図形を、元画像の位置・サイズに合わせて EMU 座標へスケールして追加する。"""
    x, y, bw, bh = det.bbox
    sx = pic_width / img_w
    sy = pic_height / img_h

    left = Emu(int(pic_left + x * sx))
    top = Emu(int(pic_top + y * sy))
    width = Emu(int(bw * sx))
    height = Emu(int(bh * sy))

    enum = _PPTX_SHAPE_ENUM.get(det.kind, MSO_SHAPE.RECTANGLE)
    shape = slide_shapes.add_shape(enum, left, top, max(1, width), max(1, height))
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(*det.fill)
    shape.line.color.rgb = RGBColor(*det.line)
    shape.name = f"Vectorized-{det.kind}"
    return shape


def detect_shapes_in_region(
    image_bgr: np.ndarray,
    region_px: Tuple[int, int, int, int],
    **detect_kwargs,
) -> List[DetectedShape]:
    """画像の指定矩形領域だけを対象に図形を検出し、座標を画像全体系に戻して返す。

    範囲を限定することで、画像全体の自動検出より誤検出が大幅に減る。
    region_px は画像ピクセル座標系の (x, y, w, h)。
    """
    x, y, w, h = region_px
    H, W = image_bgr.shape[:2]
    x0 = max(0, min(x, W - 1)); y0 = max(0, min(y, H - 1))
    x1 = max(x0 + 1, min(x + w, W)); y1 = max(y0 + 1, min(y + h, H))
    crop = image_bgr[y0:y1, x0:x1]
    ok, buf = cv2.imencode(".png", crop)
    if not ok:
        return []
    # 範囲指定時はノイズ下限を緩め、範囲いっぱいの図形も拾えるよう上限を 1.0 に
    detect_kwargs.setdefault("min_area_ratio", 0.01)
    detect_kwargs.setdefault("max_area_ratio", 1.0)
    shapes = detect_shapes_in_image(buf.tobytes(), **detect_kwargs)
    # crop 内座標 -> 画像全体座標へオフセット
    out: List[DetectedShape] = []
    for s in shapes:
        bx, by, bw, bh = s.bbox
        out.append(DetectedShape(
            kind=s.kind, bbox=(bx + x0, by + y0, bw, bh),
            fill=s.fill, line=s.line, vertices=s.vertices,
        ))
    return out


def add_detected_shape(
    slide_shapes, det: DetectedShape,
    pic_left: int, pic_top: int, pic_width: int, pic_height: int,
    img_w: int, img_h: int,
):
    """検出図形を編集可能なオートシェイプとしてスライドに追加する（公開ラッパ）。"""
    return _add_shape_for_detection(
        slide_shapes, det, pic_left, pic_top, pic_width, pic_height, img_w, img_h,
    )


# ---------------------------------------------------------------------------
# プリミティブ（線・円・塗り矩形）検出 — 線画ダイアグラム向けの高品質トレース
# ---------------------------------------------------------------------------

@dataclass
class Primitive:
    """検出した 1 つの編集可能プリミティブ（画像ピクセル座標系）。"""

    kind: str                       # "line" | "circle" | "rect"
    color: Tuple[int, int, int]     # RGB（線色 or 塗り色）
    # line の場合は (x1, y1, x2, y2)、circle/rect の場合は (x, y, w, h)
    coords: Tuple[int, int, int, int]
    filled: bool = False            # rect が塗りつぶしかどうか


def _line_color(image_bgr: np.ndarray, x1, y1, x2, y2) -> Tuple[int, int, int]:
    """線分上をサンプリングして代表色（中央値）を RGB で返す。"""
    n = 20
    xs = np.linspace(x1, x2, n).astype(int)
    ys = np.linspace(y1, y2, n).astype(int)
    H, W = image_bgr.shape[:2]
    xs = np.clip(xs, 0, W - 1); ys = np.clip(ys, 0, H - 1)
    pix = image_bgr[ys, xs]
    b, g, r = np.median(pix, axis=0)
    return (int(r), int(g), int(b))


def _circle_color(image_bgr: np.ndarray, cx, cy, rad) -> Tuple[int, int, int]:
    """円周付近を複数半径でサンプリングし、最も濃い（=線らしい）色を RGB で返す。"""
    H, W = image_bgr.shape[:2]
    ang = np.linspace(0, 2 * math.pi, 72)
    best = (255, 255, 255)
    best_lum = 1e9
    for fr in (0.88, 0.94, 1.0, 1.06):
        r_ = rad * fr
        xs = np.clip((cx + r_ * np.cos(ang)).astype(int), 0, W - 1)
        ys = np.clip((cy + r_ * np.sin(ang)).astype(int), 0, H - 1)
        pix = image_bgr[ys, xs]
        b, g, r = np.median(pix, axis=0)
        lum = 0.114 * b + 0.587 * g + 0.299 * r
        if lum < best_lum:
            best_lum = lum
            best = (int(r), int(g), int(b))
    return best


def _is_near_white(color: Tuple[int, int, int], thresh: int = 238) -> bool:
    """白に近い（背景と同化して見えない）色か。"""
    return all(c >= thresh for c in color)


def _merge_collinear(lines, angle_tol=8.0, dist_tol=12.0):
    """近接・同方向の線分をまとめて本数を減らす（端点ベースの素朴なマージ）。"""
    segs = []
    for x1, y1, x2, y2 in lines:
        ang = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180
        length = math.hypot(x2 - x1, y2 - y1)
        segs.append([x1, y1, x2, y2, ang, length])
    segs.sort(key=lambda s: -s[5])  # 長い順
    kept = []
    for s in segs:
        x1, y1, x2, y2, ang, ln = s
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        dup = False
        for k in kept:
            kang = k[4]
            da = abs(ang - kang)
            da = min(da, 180 - da)
            if da > angle_tol:
                continue
            kmx, kmy = (k[0] + k[2]) / 2, (k[1] + k[3]) / 2
            if math.hypot(mx - kmx, my - kmy) < dist_tol:
                dup = True
                break
        if not dup:
            kept.append(s)
    return [(int(s[0]), int(s[1]), int(s[2]), int(s[3])) for s in kept]


def detect_primitives_in_region(
    image_bgr: np.ndarray,
    region_px: Tuple[int, int, int, int],
    *,
    min_line_frac: float = 0.18,
    max_lines: int = 40,
) -> List[Primitive]:
    """指定範囲内の線・円・塗り矩形を個別に検出する（線画ダイアグラム向け）。

    線は塗りなしの直線、円は塗りなしの楕円、はっきり塗られた矩形のみ塗り矩形として
    返す。座標は画像全体系。範囲全体を 1 つの灰色矩形に潰さないのが従来との違い。
    """
    x, y, w, h = region_px
    H, W = image_bgr.shape[:2]
    x0 = max(0, min(x, W - 1)); y0 = max(0, min(y, H - 1))
    x1 = max(x0 + 1, min(x + w, W)); y1 = max(y0 + 1, min(y + h, H))
    crop = image_bgr[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)

    prims: List[Primitive] = []

    # --- 円（Hough）---
    min_dim = min(cw, ch)
    circles = cv2.HoughCircles(
        blur, cv2.HOUGH_GRADIENT, dp=1.2, minDist=min_dim * 0.2,
        param1=120, param2=40,
        minRadius=int(min_dim * 0.04), maxRadius=int(min_dim * 0.55),
    )
    circle_mask_centers = []
    if circles is not None:
        for cx, cy, rad in np.round(circles[0]).astype(int):
            color = _circle_color(crop, cx, cy, rad)
            if _is_near_white(color):
                continue  # 白い円＝背景に同化して見えない → 捨てる
            prims.append(Primitive(
                "circle", color,
                (x0 + cx - rad, y0 + cy - rad, 2 * rad, 2 * rad),
            ))
            circle_mask_centers.append((cx, cy, rad))

    # --- 塗り矩形（内部が均一色のものだけ）---
    edges = cv2.Canny(blur, 50, 150)
    edges_d = cv2.dilate(edges, np.ones((3, 3), np.uint8), 1)
    contours, _ = cv2.findContours(edges_d, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    crop_area = cw * ch
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < crop_area * 0.02 or area > crop_area * 0.98:
            continue
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.03 * peri, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        rx, ry, rw, rh = cv2.boundingRect(approx)
        # 内部 60% 領域の色ばらつきが小さい＝塗りつぶし矩形とみなす
        inner = crop[ry + rh // 5: ry + rh - rh // 5, rx + rw // 5: rx + rw - rw // 5]
        if inner.size == 0:
            continue
        if inner.reshape(-1, 3).std(axis=0).mean() > 18:
            continue  # 内部が一様でない（=ただの枠や複雑領域）→ 矩形化しない
        b, g, r = np.median(inner.reshape(-1, 3), axis=0)
        prims.append(Primitive(
            "rect", (int(r), int(g), int(b)),
            (x0 + rx, y0 + ry, rw, rh), filled=True,
        ))

    # --- 直線（Hough）---
    min_len = max(20, int(min(cw, ch) * min_line_frac))
    hough = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=60,
        minLineLength=min_len, maxLineGap=10,
    )
    if hough is not None:
        raw = [tuple(l[0]) for l in hough]
        for (lx1, ly1, lx2, ly2) in _merge_collinear(raw)[:max_lines]:
            color = _line_color(crop, lx1, ly1, lx2, ly2)
            if _is_near_white(color):
                continue  # 白い線＝背景に同化 → 捨てる
            prims.append(Primitive(
                "line", color,
                (x0 + lx1, y0 + ly1, x0 + lx2, y0 + ly2),
            ))

    return prims


def add_primitive(
    slide_shapes, prim: Primitive,
    pic_left: int, pic_top: int, pic_width: int, pic_height: int,
    img_w: int, img_h: int,
):
    """検出プリミティブを編集可能オブジェクトとしてスライドに追加する。"""
    sx = pic_width / img_w
    sy = pic_height / img_h

    def ex(v):  # 画像px(横) -> スライドEMU
        return Emu(int(pic_left + v * sx))

    def ey(v):
        return Emu(int(pic_top + v * sy))

    if prim.kind == "line":
        lx1, ly1, lx2, ly2 = prim.coords
        conn = slide_shapes.add_connector(MSO_CONNECTOR.STRAIGHT, ex(lx1), ey(ly1), ex(lx2), ey(ly2))
        conn.line.color.rgb = RGBColor(*prim.color)
        conn.line.width = Pt(1.5)
        conn.name = "Traced-line"
        return conn

    x, y, w, h = prim.coords
    left, top = ex(x), ey(y)
    width = Emu(max(1, int(w * sx)))
    height = Emu(max(1, int(h * sy)))
    enum = MSO_SHAPE.OVAL if prim.kind == "circle" else MSO_SHAPE.RECTANGLE
    shape = slide_shapes.add_shape(enum, left, top, width, height)
    if prim.kind == "circle" or not prim.filled:
        shape.fill.background()                       # 塗りなし＝中身を隠さない
        shape.line.color.rgb = RGBColor(*prim.color)
        shape.line.width = Pt(1.5)
    else:
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor(*prim.color)
        shape.line.fill.background()
    shape.name = f"Traced-{prim.kind}"
    return shape


def representative_color(image_bgr: np.ndarray, bbox: Tuple[int, int, int, int]) -> Tuple[int, int, int]:
    """画像の指定矩形領域の代表色（中央値）を RGB で返す。

    平均ではなく中央値を使うことで、縁のアンチエイリアスやノイズの影響を抑え、
    領域内で支配的な色を拾いやすくする。image_bgr は OpenCV の BGR 画像。
    """
    x, y, w, h = bbox
    h_img, w_img = image_bgr.shape[:2]
    x0 = max(0, min(x, w_img - 1))
    y0 = max(0, min(y, h_img - 1))
    x1 = max(x0 + 1, min(x + w, w_img))
    y1 = max(y0 + 1, min(y + h, h_img))
    patch = image_bgr[y0:y1, x0:x1].reshape(-1, 3)
    if patch.size == 0:
        return (255, 255, 255)
    b, g, r = np.median(patch, axis=0)
    return (int(r), int(g), int(b))


def add_editable_rectangle(
    slide_shapes,
    region_px: Tuple[int, int, int, int],
    pic_left: int,
    pic_top: int,
    pic_width: int,
    pic_height: int,
    img_w: int,
    img_h: int,
    fill_rgb: Tuple[int, int, int],
    *,
    line_rgb: Optional[Tuple[int, int, int]] = None,
    name: str = "Region",
):
    """ユーザーが選択した画像内領域を、編集可能な矩形オートシェイプとして追加する。

    region_px は画像ピクセル座標系の (x, y, w, h)。画像上の位置を、スライド上での
    画像の表示位置・サイズ（EMU）に合わせてスケールして配置する。

    Returns:
        追加した shape オブジェクト。
    """
    x, y, w, h = region_px
    sx = pic_width / img_w
    sy = pic_height / img_h

    left = Emu(int(pic_left + x * sx))
    top = Emu(int(pic_top + y * sy))
    width = Emu(max(1, int(w * sx)))
    height = Emu(max(1, int(h * sy)))

    shape = slide_shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(*fill_rgb)
    if line_rgb is None:
        shape.line.fill.background()  # 枠線なし
    else:
        shape.line.color.rgb = RGBColor(*line_rgb)
    shape.name = name
    return shape


def convert_presentation(
    input_path: str,
    output_path: str,
    *,
    remove_original_picture: bool = False,
    progress=None,
    **detect_kwargs,
) -> ConversionReport:
    """PPTX を読み込み、各画像内の図形をオートシェイプに変換して保存する。

    Args:
        input_path: 入力 .pptx パス。
        output_path: 出力 .pptx パス。
        remove_original_picture: True なら元の画像を削除する（既定は残す）。
        progress: callable(done, total, message) 形式の進捗コールバック（任意）。
        **detect_kwargs: detect_shapes_in_image へ渡す検出パラメータ。

    Returns:
        ConversionReport。
    """
    prs = Presentation(input_path)
    report = ConversionReport()

    slides = list(prs.slides)
    total = len(slides)
    for idx, slide in enumerate(slides):
        created_in_slide = 0
        # イテレート中に追加・削除するのでリスト化
        pictures = [s for s in slide.shapes if s.shape_type == 13]  # 13 = PICTURE
        for pic in pictures:
            try:
                image_bytes = pic.image.blob
            except Exception:
                continue
            shapes = detect_shapes_in_image(image_bytes, **detect_kwargs)
            if not shapes:
                continue

            arr = np.frombuffer(image_bytes, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                continue
            img_h, img_w = img.shape[:2]

            for det in shapes:
                _add_shape_for_detection(
                    slide.shapes, det,
                    pic.left, pic.top, pic.width, pic.height,
                    img_w, img_h,
                )
                created_in_slide += 1

            report.pictures_processed += 1

            if remove_original_picture:
                pic._element.getparent().remove(pic._element)

        report.shapes_created += created_in_slide
        report.per_slide.append(created_in_slide)
        if progress:
            progress(idx + 1, total, f"スライド {idx + 1}/{total}: 図形 {created_in_slide} 個")

    prs.save(output_path)
    return report
