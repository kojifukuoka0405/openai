"""tkinter を使った簡易 GUI。

`python -m pptx_vectorizer.gui` で起動する。ファイル選択 → 変換 → 結果表示。
"""

from __future__ import annotations

import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .core import convert_presentation


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PPTX 画像→図形 変換ツール")
        self.geometry("560x300")
        self.resizable(False, False)

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.remove_var = tk.BooleanVar(value=False)

        self._build_widgets()

    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 6}

        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, padx=12, pady=12)

        ttk.Label(frm, text="入力 PPTX:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.input_var, width=48).grid(row=0, column=1, **pad)
        ttk.Button(frm, text="選択", command=self._choose_input).grid(row=0, column=2, **pad)

        ttk.Label(frm, text="出力 PPTX:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.output_var, width=48).grid(row=1, column=1, **pad)
        ttk.Button(frm, text="選択", command=self._choose_output).grid(row=1, column=2, **pad)

        ttk.Checkbutton(
            frm, text="変換後に元の画像を削除する", variable=self.remove_var
        ).grid(row=2, column=1, sticky="w", **pad)

        self.run_btn = ttk.Button(frm, text="変換実行", command=self._run)
        self.run_btn.grid(row=3, column=1, sticky="w", **pad)

        self.progress = ttk.Progressbar(frm, mode="determinate", length=420)
        self.progress.grid(row=4, column=0, columnspan=3, **pad)

        self.status = ttk.Label(frm, text="待機中", foreground="#555")
        self.status.grid(row=5, column=0, columnspan=3, sticky="w", **pad)

    def _choose_input(self) -> None:
        path = filedialog.askopenfilename(
            title="入力 PPTX を選択", filetypes=[("PowerPoint", "*.pptx")]
        )
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                p = Path(path)
                self.output_var.set(str(p.with_name(p.stem + "_vectorized.pptx")))

    def _choose_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="出力 PPTX を保存", defaultextension=".pptx",
            filetypes=[("PowerPoint", "*.pptx")],
        )
        if path:
            self.output_var.set(path)

    def _set_progress(self, done: int, total: int, message: str) -> None:
        self.progress["maximum"] = total
        self.progress["value"] = done
        self.status.config(text=message)
        self.update_idletasks()

    def _run(self) -> None:
        inp, out = self.input_var.get(), self.output_var.get()
        if not inp or not out:
            messagebox.showwarning("入力不足", "入力と出力のファイルを指定してください。")
            return
        self.run_btn.config(state="disabled")
        self.status.config(text="変換中...")
        threading.Thread(target=self._worker, args=(inp, out), daemon=True).start()

    def _worker(self, inp: str, out: str) -> None:
        try:
            report = convert_presentation(
                inp, out,
                remove_original_picture=self.remove_var.get(),
                progress=lambda d, t, m: self.after(0, self._set_progress, d, t, m),
            )
            self.after(0, self._done, report, out)
        except Exception:  # noqa: BLE001
            err = traceback.format_exc()
            self.after(0, self._fail, err)

    def _done(self, report, out: str) -> None:
        self.run_btn.config(state="normal")
        self.status.config(text="完了")
        messagebox.showinfo(
            "完了",
            f"画像 {report.pictures_processed} 枚を処理し、"
            f"図形 {report.shapes_created} 個を生成しました。\n\n保存先: {out}",
        )

    def _fail(self, err: str) -> None:
        self.run_btn.config(state="normal")
        self.status.config(text="エラー")
        messagebox.showerror("エラー", f"変換に失敗しました。\n\n{err}")


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
