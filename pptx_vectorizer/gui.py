"""対話型 GUI: PPTX 内の画像をプレビューし、ドラッグで囲った範囲だけを
編集可能な矩形オブジェクトに変換する。

`python -m pptx_vectorizer.gui` で起動。

操作の流れ:
  1. 「PPTX を開く」で読み込む
  2. キャンバスに表示された画像の上を **ドラッグ** して変換したい範囲を囲む
     （複数可。スライドに複数画像があれば「前/次の画像」で切り替え）
  3. 「変換して保存」で、囲った範囲が編集可能な矩形オートシェイプになった
     新しい PPTX を書き出す
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk
from pptx import Presentation

from .core import (
    add_editable_rectangle,
    representative_color,
    detect_shapes_in_region,
    add_detected_shape,
)

# キャンバスに表示する画像の最大サイズ（ピクセル）
MAX_VIEW_W = 960
MAX_VIEW_H = 620


@dataclass
class PictureItem:
    """PPTX 内の 1 つの画像と、それに対するユーザー選択範囲を保持する。"""

    slide_index: int
    slide: object              # pptx slide
    pic: object                # pptx Picture shape
    image_bgr: np.ndarray      # OpenCV BGR 画像
    regions: List[Tuple[int, int, int, int]] = field(default_factory=list)  # 画像px (x,y,w,h)

    @property
    def size(self) -> Tuple[int, int]:
        h, w = self.image_bgr.shape[:2]
        return w, h


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PPTX 範囲選択 → 編集可能オブジェクト変換")
        self.geometry("1100x780")

        self.prs = None
        self.input_path: Optional[str] = None
        self.items: List[PictureItem] = []
        self.cur = 0

        # 表示スケールと現在ドラッグ中の矩形
        self.scale = 1.0
        self._tk_img = None
        self._drag_start: Optional[Tuple[int, int]] = None
        self._rubber = None

        self._build_widgets()
        self._update_state()

    # ----- UI 構築 -----
    def _build_widgets(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)

        ttk.Button(top, text="PPTX を開く", command=self._open).pack(side="left")
        self.save_btn = ttk.Button(top, text="変換して保存", command=self._save, state="disabled")
        self.save_btn.pack(side="left", padx=6)

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)

        self.prev_btn = ttk.Button(top, text="◀ 前の画像", command=self._prev, state="disabled")
        self.prev_btn.pack(side="left")
        self.next_btn = ttk.Button(top, text="次の画像 ▶", command=self._next, state="disabled")
        self.next_btn.pack(side="left", padx=6)

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)

        self.undo_btn = ttk.Button(top, text="直前の範囲を取消", command=self._undo, state="disabled")
        self.undo_btn.pack(side="left")
        self.clear_btn = ttk.Button(top, text="この画像の範囲を全消去", command=self._clear, state="disabled")
        self.clear_btn.pack(side="left", padx=6)

        self.trace_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            top, text="範囲内の図形をトレース（推奨）", variable=self.trace_var
        ).pack(side="left", padx=12)

        self.remove_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top, text="変換後に元画像を削除", variable=self.remove_var
        ).pack(side="left")

        # キャンバス（スクロール対応）
        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, padx=10, pady=4)
        self.canvas = tk.Canvas(mid, bg="#2b2b2b", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        # 右クリックで直近の範囲を削除
        self.canvas.bind("<ButtonPress-3>", lambda e: self._undo())

        self.status = ttk.Label(self, text="PPTX を開いてください", anchor="w")
        self.status.pack(fill="x", padx=10, pady=6)

    # ----- ファイル操作 -----
    def _open(self) -> None:
        path = filedialog.askopenfilename(
            title="PPTX を開く", filetypes=[("PowerPoint", "*.pptx")]
        )
        if not path:
            return
        try:
            prs = Presentation(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("エラー", f"読み込みに失敗しました:\n{exc}")
            return

        items: List[PictureItem] = []
        for si, slide in enumerate(prs.slides):
            for shape in slide.shapes:
                if shape.shape_type != 13:  # PICTURE
                    continue
                try:
                    blob = shape.image.blob
                except Exception:
                    continue
                arr = np.frombuffer(blob, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is None:
                    continue
                items.append(PictureItem(si, slide, shape, img))

        if not items:
            messagebox.showinfo("情報", "このファイルには変換できる画像がありませんでした。")
            return

        self.prs = prs
        self.input_path = path
        self.items = items
        self.cur = 0
        self._render()
        self._update_state()

    def _save(self) -> None:
        total_regions = sum(len(it.regions) for it in self.items)
        if total_regions == 0:
            messagebox.showwarning("範囲が未指定", "変換する範囲をドラッグで指定してください。")
            return

        default = ""
        if self.input_path:
            p = Path(self.input_path)
            default = str(p.with_name(p.stem + "_edited.pptx"))
        out = filedialog.asksaveasfilename(
            title="保存先", defaultextension=".pptx",
            initialfile=Path(default).name if default else None,
            filetypes=[("PowerPoint", "*.pptx")],
        )
        if not out:
            return

        created = 0
        empty_regions = 0
        trace = self.trace_var.get()
        try:
            for it in self.items:
                if not it.regions:
                    continue
                img_w, img_h = it.size
                geom = (it.pic.left, it.pic.top, it.pic.width, it.pic.height)
                for i, region in enumerate(it.regions, 1):
                    if trace:
                        dets = detect_shapes_in_region(it.image_bgr, region)
                        if not dets:
                            empty_regions += 1
                            continue
                        for det in dets:
                            add_detected_shape(
                                it.slide.shapes, det, *geom, img_w, img_h,
                            )
                            created += 1
                    else:
                        color = representative_color(it.image_bgr, region)
                        add_editable_rectangle(
                            it.slide.shapes, region, *geom, img_w, img_h, color,
                            name=f"Region-s{it.slide_index + 1}-{i}",
                        )
                        created += 1
                if self.remove_var.get():
                    it.pic._element.getparent().remove(it.pic._element)
            self.prs.save(out)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("エラー", f"保存に失敗しました:\n{exc}")
            return

        msg = f"{created} 個の編集可能なオブジェクトを作成しました。\n\n保存先: {out}"
        if trace and empty_regions:
            msg += (
                f"\n\n※ {empty_regions} 個の範囲では図形を検出できませんでした。"
                "\n  輪郭がはっきりした図形を含むように囲み直すか、"
                "\n  「範囲内の図形をトレース」のチェックを外すと"
                "\n  範囲全体を単色矩形に変換します。"
            )
        messagebox.showinfo("完了", msg)

    # ----- 表示 -----
    def _render(self) -> None:
        self.canvas.delete("all")
        if not self.items:
            return
        it = self.items[self.cur]
        w, h = it.size
        self.scale = min(MAX_VIEW_W / w, MAX_VIEW_H / h, 1.0)
        disp_w, disp_h = int(w * self.scale), int(h * self.scale)

        rgb = cv2.cvtColor(it.image_bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb).resize((disp_w, disp_h), Image.LANCZOS)
        self._tk_img = ImageTk.PhotoImage(pil)
        self.canvas.config(scrollregion=(0, 0, disp_w, disp_h))
        self.canvas.create_image(0, 0, anchor="nw", image=self._tk_img, tags="img")

        # 既存の選択範囲を描画
        for idx, (x, y, rw, rh) in enumerate(it.regions, 1):
            self._draw_region(x, y, rw, rh, idx)

    def _draw_region(self, x: int, y: int, rw: int, rh: int, idx: int) -> None:
        s = self.scale
        x0, y0, x1, y1 = x * s, y * s, (x + rw) * s, (y + rh) * s
        self.canvas.create_rectangle(
            x0, y0, x1, y1, outline="#33ff99", width=2, tags="region"
        )
        self.canvas.create_text(
            x0 + 4, y0 + 4, anchor="nw", text=str(idx),
            fill="#33ff99", font=("", 11, "bold"), tags="region",
        )

    # ----- マウス操作（範囲ドラッグ）-----
    def _canvas_xy(self, event) -> Tuple[float, float]:
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def _on_press(self, event) -> None:
        if not self.items:
            return
        self._drag_start = self._canvas_xy(event)
        if self._rubber:
            self.canvas.delete(self._rubber)
        self._rubber = self.canvas.create_rectangle(
            *self._drag_start, *self._drag_start, outline="#ffcc00", width=2, dash=(4, 3)
        )

    def _on_drag(self, event) -> None:
        if not self._drag_start or self._rubber is None:
            return
        x0, y0 = self._drag_start
        x1, y1 = self._canvas_xy(event)
        self.canvas.coords(self._rubber, x0, y0, x1, y1)

    def _on_release(self, event) -> None:
        if not self._drag_start:
            return
        x0, y0 = self._drag_start
        x1, y1 = self._canvas_xy(event)
        self._drag_start = None
        if self._rubber:
            self.canvas.delete(self._rubber)
            self._rubber = None

        # キャンバス座標 -> 画像ピクセル座標
        it = self.items[self.cur]
        w, h = it.size
        s = self.scale
        px0, px1 = sorted((x0 / s, x1 / s))
        py0, py1 = sorted((y0 / s, y1 / s))
        px0 = max(0, min(px0, w)); px1 = max(0, min(px1, w))
        py0 = max(0, min(py0, h)); py1 = max(0, min(py1, h))
        rw, rh = int(px1 - px0), int(py1 - py0)
        if rw < 4 or rh < 4:  # 小さすぎる選択は無視
            return
        it.regions.append((int(px0), int(py0), rw, rh))
        self._draw_region(int(px0), int(py0), rw, rh, len(it.regions))
        self._update_state()

    # ----- ナビゲーション/編集 -----
    def _prev(self) -> None:
        if self.cur > 0:
            self.cur -= 1
            self._render()
            self._update_state()

    def _next(self) -> None:
        if self.cur < len(self.items) - 1:
            self.cur += 1
            self._render()
            self._update_state()

    def _undo(self) -> None:
        if self.items and self.items[self.cur].regions:
            self.items[self.cur].regions.pop()
            self._render()
            self._update_state()

    def _clear(self) -> None:
        if self.items and self.items[self.cur].regions:
            self.items[self.cur].regions.clear()
            self._render()
            self._update_state()

    def _update_state(self) -> None:
        has = bool(self.items)
        self.prev_btn.config(state="normal" if has and self.cur > 0 else "disabled")
        self.next_btn.config(state="normal" if has and self.cur < len(self.items) - 1 else "disabled")
        cur_regions = self.items[self.cur].regions if has else []
        self.undo_btn.config(state="normal" if cur_regions else "disabled")
        self.clear_btn.config(state="normal" if cur_regions else "disabled")
        total = sum(len(it.regions) for it in self.items)
        self.save_btn.config(state="normal" if total else "disabled")

        if not has:
            self.status.config(text="PPTX を開いてください")
            return
        it = self.items[self.cur]
        self.status.config(
            text=(
                f"画像 {self.cur + 1}/{len(self.items)}"
                f"（スライド {it.slide_index + 1}）  "
                f"この画像の範囲: {len(cur_regions)} 個 / 合計 {total} 個   "
                f"— ドラッグで範囲を囲む / 右クリックで直前を取消"
            )
        )


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
