"""音声文字起こしアプリの GUI。

`python -m audio_transcriber.gui` で起動。

操作の流れ:
  1. 「音声ファイルを開く」で MP3 / WAV などを選ぶ
  2. （初回のみ）OpenAI の API キーを入力する
  3. モデルを選ぶ（起動時に取得した最新料金と概算費用が出る）
  4. 「文字起こし開始」を押す
  5. 「文字起こし」タブに素のテキスト、「推敲版」タブに読みやすく整えた版が出る
  6. 「保存」でテキストファイルとして書き出す
"""

from __future__ import annotations

import queue
import threading
import traceback
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import audio, polish as polish_mod, pricing, providers, transcribe as transcribe_mod
from .config import forget_api_key, resolve_api_key, save_api_key, stored_keys
from .core import (
    Options,
    Result,
    TranscriberError,
    format_duration,
    make_client,
    transcribe_and_polish,
    write_outputs,
)

STYLE_LABELS = {
    "読みやすい書き言葉": "readable",
    "話し言葉のまま（最小限）": "verbatim",
    "記事・議事録スタイル": "article",
}

FILETYPES = [
    ("音声ファイル", "*.mp3 *.wav *.m4a *.mp4 *.flac *.ogg *.oga *.webm *.mpga *.mpeg"),
    ("MP3", "*.mp3"),
    ("WAV", "*.wav"),
    ("すべてのファイル", "*.*"),
]


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("音声文字起こし ＆ 推敲")
        self.geometry("1000x720")
        self.minsize(820, 600)

        self.audio_path: Optional[Path] = None
        self.audio_duration: Optional[float] = None
        self.result: Optional[Result] = None
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        # 価格が届くまでは参考価格で表示しておく
        self.prices = pricing.PriceTable(prices=dict(pricing.BUILTIN_PRICES), origin="builtin")
        self.provider_keys: dict = dict(stored_keys())
        self.key_provider: Optional[str] = None

        self._build_widgets()
        self.after(100, self._drain_queue)
        self.refresh_prices()          # 起動のたびに最新料金を取りにいく

    # ------------------------------------------------------------------ UI
    def _build_widgets(self) -> None:
        pad = {"padx": 6, "pady": 4}

        # --- ファイル選択 ---
        top = ttk.Frame(self)
        top.pack(fill=tk.X, **pad)
        ttk.Button(top, text="音声ファイルを開く", command=self.open_file).pack(side=tk.LEFT)
        self.file_label = ttk.Label(top, text="ファイル未選択")
        self.file_label.pack(side=tk.LEFT, padx=10)

        # --- API キー（選んだモデルの会社ぶんだけ表示する） ---
        self.key_frame = ttk.LabelFrame(self, text="API キー")
        self.key_frame.pack(fill=tk.X, **pad)

        self.transcribe_key_row = ttk.Frame(self.key_frame)
        self.transcribe_key_label = ttk.Label(self.transcribe_key_row, width=24)
        self.transcribe_key_label.pack(side=tk.LEFT)
        self.transcribe_key_var = tk.StringVar()
        ttk.Entry(
            self.transcribe_key_row, textvariable=self.transcribe_key_var,
            show="*", width=52,
        ).pack(side=tk.LEFT, padx=6)
        self.save_transcribe_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.transcribe_key_row, text="このPCに保存",
            variable=self.save_transcribe_key_var,
        ).pack(side=tk.LEFT)
        self.transcribe_key_hint = ttk.Label(self.transcribe_key_row, foreground="#666666")
        self.transcribe_key_hint.pack(side=tk.LEFT, padx=6)

        self.openai_key_row = ttk.Frame(self.key_frame)
        self.openai_key_row.pack(fill=tk.X, padx=6, pady=4)
        self.openai_key_label = ttk.Label(self.openai_key_row, width=24)
        self.openai_key_label.pack(side=tk.LEFT)
        self.openai_key_var = tk.StringVar(value=stored_keys().get("openai", ""))
        ttk.Entry(
            self.openai_key_row, textvariable=self.openai_key_var, show="*", width=52,
        ).pack(side=tk.LEFT, padx=6)
        # 環境変数から取れている場合まで勝手にファイル保存はしない
        self.save_openai_key_var = tk.BooleanVar(value=bool(stored_keys().get("openai")))
        ttk.Checkbutton(
            self.openai_key_row, text="このPCに保存", variable=self.save_openai_key_var,
        ).pack(side=tk.LEFT)
        ttk.Label(
            self.openai_key_row, text="環境変数があればそちらを使います",
            foreground="#666666",
        ).pack(side=tk.LEFT, padx=6)

        # --- モデルと料金 ---
        models = ttk.LabelFrame(self, text="モデルと料金")
        models.pack(fill=tk.X, **pad)

        price_row = ttk.Frame(models)
        price_row.pack(fill=tk.X, padx=6, pady=4)
        self.price_label = ttk.Label(price_row, text="料金を取得しています…", foreground="#666666")
        self.price_label.pack(side=tk.LEFT)
        ttk.Button(price_row, text="価格を再取得", command=self.refresh_prices).pack(side=tk.RIGHT)

        model_row = ttk.Frame(models)
        model_row.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(model_row, text="文字起こし:", width=12).pack(side=tk.LEFT)
        self.transcribe_model_var = tk.StringVar()
        self.transcribe_combo = ttk.Combobox(
            model_row, textvariable=self.transcribe_model_var, state="readonly"
        )
        self.transcribe_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.transcribe_combo.bind("<<ComboboxSelected>>", lambda _e: self.on_model_changed())

        polish_row = ttk.Frame(models)
        polish_row.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(polish_row, text="推敲:", width=12).pack(side=tk.LEFT)
        self.polish_model_var = tk.StringVar()
        self.polish_combo = ttk.Combobox(
            polish_row, textvariable=self.polish_model_var, state="readonly"
        )
        self.polish_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.polish_combo.bind("<<ComboboxSelected>>", lambda _e: self.update_estimate())

        self.estimate_label = ttk.Label(models, text="概算費用: ファイルを選ぶと表示されます")
        self.estimate_label.pack(anchor=tk.W, padx=6, pady=(0, 6))

        # --- オプション ---
        opts = ttk.LabelFrame(self, text="設定")
        opts.pack(fill=tk.X, **pad)

        row1 = ttk.Frame(opts)
        row1.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(row1, text="言語:").pack(side=tk.LEFT)
        self.language_var = tk.StringVar(value="自動判定")
        ttk.Combobox(
            row1, textvariable=self.language_var, width=10, state="readonly",
            values=["自動判定", "ja", "en", "zh", "ko", "fr", "de", "es"],
        ).pack(side=tk.LEFT, padx=(4, 16))

        ttk.Label(row1, text="推敲スタイル:").pack(side=tk.LEFT)
        self.style_var = tk.StringVar(value="読みやすい書き言葉")
        ttk.Combobox(
            row1, textvariable=self.style_var, width=22, state="readonly",
            values=list(STYLE_LABELS),
        ).pack(side=tk.LEFT, padx=(4, 16))

        self.polish_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            row1, text="推敲版も作る", variable=self.polish_var,
            command=self.update_estimate,
        ).pack(side=tk.LEFT)

        row2 = ttk.Frame(opts)
        row2.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(row2, text="固有名詞・専門用語（カンマ区切り）:").pack(side=tk.LEFT)
        self.keywords_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.keywords_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=6
        )

        row3 = ttk.Frame(opts)
        row3.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(row3, text="推敲への追加指示（任意）:").pack(side=tk.LEFT)
        self.instructions_var = tk.StringVar()
        ttk.Entry(row3, textvariable=self.instructions_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=6
        )

        # --- 実行 ---
        run = ttk.Frame(self)
        run.pack(fill=tk.X, **pad)
        self.run_button = ttk.Button(run, text="文字起こし開始", command=self.start)
        self.run_button.pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(run, mode="determinate", maximum=100)
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
        self.status_label = ttk.Label(run, text="待機中")
        self.status_label.pack(side=tk.LEFT)

        # --- 結果 ---
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, **pad)
        self.raw_text = self._add_tab("文字起こし（そのまま）")
        self.polished_text = self._add_tab("推敲版")

        bottom = ttk.Frame(self)
        bottom.pack(fill=tk.X, **pad)
        ttk.Button(bottom, text="表示中のタブをコピー", command=self.copy_current).pack(side=tk.LEFT)
        ttk.Button(bottom, text="両方をファイルに保存", command=self.save_outputs).pack(
            side=tk.LEFT, padx=6
        )
        self.info_label = ttk.Label(bottom, text="")
        self.info_label.pack(side=tk.LEFT, padx=10)

        # 最新料金が届くまでは、内蔵の参考価格で選択肢を作っておく
        self._fill_model_combo(
            self.transcribe_combo, self.transcribe_model_var,
            "transcribe", transcribe_mod.DEFAULT_TRANSCRIBE_MODEL,
        )
        self._fill_model_combo(
            self.polish_combo, self.polish_model_var,
            "polish", polish_mod.DEFAULT_POLISH_MODEL,
        )
        self.sync_key_rows()

    def _add_tab(self, title: str) -> ScrolledText:
        frame = ttk.Frame(self.notebook)
        widget = ScrolledText(frame, wrap=tk.WORD, font=("", 11), undo=True)
        widget.pack(fill=tk.BOTH, expand=True)
        self.notebook.add(frame, text=title)
        return widget

    # ------------------------------------------------------------ 料金
    def refresh_prices(self) -> None:
        """最新の料金をバックグラウンドで取得する（画面は止めない）。"""
        self.price_label.config(text="料金を取得しています…")

        def work() -> None:
            table = pricing.load_prices()
            self.queue.put(("prices", table))

        threading.Thread(target=work, daemon=True).start()

    def _apply_prices(self, table: pricing.PriceTable) -> None:
        """取得した料金を、選択肢と表示に反映する。"""
        self.prices = table
        self.price_label.config(text=table.origin_label())
        self._fill_model_combo(
            self.transcribe_combo, self.transcribe_model_var,
            "transcribe", transcribe_mod.DEFAULT_TRANSCRIBE_MODEL,
        )
        self._fill_model_combo(
            self.polish_combo, self.polish_model_var,
            "polish", polish_mod.DEFAULT_POLISH_MODEL,
        )
        self.update_estimate()

    def _fill_model_combo(self, combo: ttk.Combobox, var: tk.StringVar,
                          kind: str, default: str) -> None:
        """「既定のモデル＋いま安い 3 種類」を選択肢にする。"""
        models = pricing.selectable_models(self.prices, kind, default)
        keys = self.provider_keys if hasattr(self, "provider_keys") else None
        choices = [pricing.format_choice(self.prices, m, keys) for m in models]
        current = pricing.model_from_choice(var.get()) if var.get() else default
        combo["values"] = choices
        for choice, model in zip(choices, models):
            if model.name == current:
                var.set(choice)
                return
        var.set(choices[0] if choices else default)

    def selected_model(self, var: tk.StringVar, default: str) -> str:
        return pricing.model_from_choice(var.get()) if var.get() else default

    def on_model_changed(self) -> None:
        """文字起こしモデルが変わったとき: キー欄と概算を更新する。"""
        self.sync_key_rows()
        self.update_estimate()

    def sync_key_rows(self) -> None:
        """選んだモデルの会社に合わせて、必要な API キー欄だけを出す。"""
        model = self.selected_model(
            self.transcribe_model_var, transcribe_mod.DEFAULT_TRANSCRIBE_MODEL
        )
        provider = providers.get_provider(model)

        # 入力途中のキーは会社ごとに覚えておく（切り替えても消えない）
        if self.key_provider and self.key_provider != providers.DEFAULT_PROVIDER:
            self.provider_keys[self.key_provider] = self.transcribe_key_var.get().strip()

        if provider.key == providers.DEFAULT_PROVIDER:
            self.transcribe_key_row.pack_forget()
            self.openai_key_label.config(text="OpenAI（文字起こし・推敲）")
        else:
            self.transcribe_key_row.pack(fill=tk.X, padx=6, pady=4,
                                         before=self.openai_key_row)
            self.transcribe_key_label.config(text=f"文字起こし（{provider.label}）")
            self.transcribe_key_var.set(self.provider_keys.get(provider.key, ""))
            self.save_transcribe_key_var.set(bool(stored_keys().get(provider.key)))
            has_key = providers.resolve_key(model, self.provider_keys) is not None
            self.transcribe_key_hint.config(
                text=f"{provider.env_var} でも可" if has_key
                else f"未設定：{provider.signup_url}"
            )
            self.openai_key_label.config(text="推敲（OpenAI）")
        self.key_provider = provider.key

    def current_api_keys(self) -> dict:
        """画面に入力されているキーをまとめる。"""
        keys = dict(self.provider_keys)
        model = self.selected_model(
            self.transcribe_model_var, transcribe_mod.DEFAULT_TRANSCRIBE_MODEL
        )
        provider = providers.provider_of(model)
        if provider != providers.DEFAULT_PROVIDER:
            keys[provider] = self.transcribe_key_var.get().strip()
        openai_key = self.openai_key_var.get().strip()
        if openai_key:
            keys["openai"] = openai_key
        return {k: v for k, v in keys.items() if v}

    def update_estimate(self) -> None:
        """選んだモデルと録音の長さから概算費用を出す。"""
        if self.audio_duration is None:
            self.estimate_label.config(
                text="概算費用: ファイルを選ぶと表示されます"
                if self.audio_path is None
                else "概算費用: 不明（長さを取得できませんでした）"
            )
            return
        transcribe_model = self.selected_model(
            self.transcribe_model_var, transcribe_mod.DEFAULT_TRANSCRIBE_MODEL
        )
        polish_model = (
            self.selected_model(self.polish_model_var, polish_mod.DEFAULT_POLISH_MODEL)
            if self.polish_var.get() else None
        )
        self.estimate_label.config(
            text=pricing.format_estimate(
                self.prices, transcribe_model, polish_model, self.audio_duration / 60
            )
        )

    # -------------------------------------------------------------- 操作
    def open_file(self) -> None:
        path = filedialog.askopenfilename(title="音声ファイルを選択", filetypes=FILETYPES)
        if not path:
            return
        selected = Path(path)
        try:
            audio.check_supported(selected)
        except audio.AudioError as exc:
            messagebox.showerror("開けません", str(exc))
            return
        self.audio_path = selected
        size_mb = selected.stat().st_size / (1024 * 1024)
        duration = audio.probe_duration(selected)
        self.audio_duration = duration
        detail = f"{selected.name}（{size_mb:.1f}MB"
        if duration:
            detail += f" / {format_duration(duration)}"
        self.file_label.config(text=detail + "）")
        self.update_estimate()

    def start(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        if self.audio_path is None:
            messagebox.showwarning("ファイル未選択", "先に音声ファイルを開いてください。")
            return

        transcribe_model = self.selected_model(
            self.transcribe_model_var, transcribe_mod.DEFAULT_TRANSCRIBE_MODEL
        )
        api_keys = self.current_api_keys()
        do_polish = self.polish_var.get()

        # 文字起こしに使う会社のキー
        if providers.resolve_key(transcribe_model, api_keys) is None:
            messagebox.showwarning(
                "API キーが必要です", providers.missing_key_message(transcribe_model)
            )
            return
        # 推敲は OpenAI を使う
        if do_polish and not resolve_api_key(api_keys.get("openai")):
            messagebox.showwarning(
                "API キーが必要です",
                "推敲には OpenAI の API キーが必要です。\n"
                "「推敲版も作る」のチェックを外せば、文字起こしだけ実行できます。\n"
                "キーは https://platform.openai.com/api-keys で発行できます。",
            )
            return

        self._persist_keys(transcribe_model, api_keys)

        language = self.language_var.get()
        options = Options(
            transcribe_model=transcribe_model,
            polish_model=self.selected_model(
                self.polish_model_var, polish_mod.DEFAULT_POLISH_MODEL
            ),
            language=None if language == "自動判定" else language,
            keywords=tuple(
                k.strip() for k in self.keywords_var.get().split(",") if k.strip()
            ),
            style=STYLE_LABELS.get(self.style_var.get(), "readable"),
            extra_instructions=self.instructions_var.get().strip() or None,
            do_polish=do_polish,
        )

        self.result = None
        self.raw_text.delete("1.0", tk.END)
        self.polished_text.delete("1.0", tk.END)
        self.info_label.config(text="")
        self.run_button.config(state=tk.DISABLED)
        self.progress["value"] = 0
        self.status_label.config(text="開始しています…")

        path = self.audio_path
        self.worker = threading.Thread(
            target=self._work, args=(path, options, api_keys), daemon=True
        )
        self.worker.start()

    def _persist_keys(self, transcribe_model: str, api_keys: dict) -> None:
        """「このPCに保存」にチェックがあるキーだけ保存する。"""
        openai_key = api_keys.get("openai", "")
        if openai_key and self.save_openai_key_var.get():
            save_api_key(openai_key, "openai")
        elif not self.save_openai_key_var.get():
            forget_api_key("openai")

        provider = providers.provider_of(transcribe_model)
        if provider == providers.DEFAULT_PROVIDER:
            return
        key = api_keys.get(provider, "")
        if key and self.save_transcribe_key_var.get():
            save_api_key(key, provider)
        elif not self.save_transcribe_key_var.get():
            forget_api_key(provider)

    # -------------------------------------------------------- ワーカー
    def _work(self, path: Path, options: Options, api_keys: dict) -> None:
        def progress(done: int, total: int, message: str) -> None:
            self.queue.put(("progress", done, message))

        try:
            client = None
            if options.do_polish or providers.provider_of(
                options.transcribe_model
            ) == providers.DEFAULT_PROVIDER:
                client = make_client(api_keys.get("openai") or None)
            result = transcribe_and_polish(
                path, options, client=client, progress=progress, api_keys=api_keys
            )
            self.queue.put(("done", result))
        except (TranscriberError, audio.AudioError, transcribe_mod.TranscriptionError,
                polish_mod.PolishError) as exc:
            self.queue.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001 - 想定外も UI に出す
            self.queue.put(("error", f"{exc}\n\n{traceback.format_exc()}"))

    def _drain_queue(self) -> None:
        try:
            while True:
                message = self.queue.get_nowait()
                kind = message[0]
                if kind == "progress":
                    _, percent, text = message
                    self.progress["value"] = percent
                    self.status_label.config(text=text)
                elif kind == "prices":
                    self._apply_prices(message[1])
                elif kind == "done":
                    self._on_done(message[1])
                elif kind == "error":
                    self.progress["value"] = 0
                    self.status_label.config(text="エラー")
                    self.run_button.config(state=tk.NORMAL)
                    messagebox.showerror("処理に失敗しました", message[1])
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _on_done(self, result: Result) -> None:
        self.result = result
        self.raw_text.delete("1.0", tk.END)
        self.raw_text.insert("1.0", result.transcript)
        self.polished_text.delete("1.0", tk.END)
        self.polished_text.insert("1.0", result.polished or "（推敲版は作成していません）")
        self.progress["value"] = 100
        self.status_label.config(text="完了")
        self.run_button.config(state=tk.NORMAL)

        info = f"モデル: {result.transcribe_model}"
        if result.polish_model:
            info += f" ／ 推敲: {result.polish_model}"
        info += f" ／ 長さ: {format_duration(result.duration)}"
        info += f" ／ 文字数: {len(result.transcript)}"
        if result.chunk_count > 1:
            info += f" ／ 分割: {result.chunk_count}"
        self.info_label.config(text=info)

        # 実際の長さと文字数で費用を計算し直す
        self.estimate_label.config(
            text=pricing.format_estimate(
                self.prices,
                result.transcribe_model,
                result.polish_model,
                minutes=(result.duration / 60) if result.duration else None,
                chars=len(result.transcript),
            )
        )
        self.notebook.select(1 if result.polished else 0)

    # ---------------------------------------------------------- 出力
    def copy_current(self) -> None:
        widget = self.polished_text if self.notebook.index("current") == 1 else self.raw_text
        text = widget.get("1.0", tk.END).strip()
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_label.config(text="クリップボードにコピーしました")

    def save_outputs(self) -> None:
        if self.result is None:
            messagebox.showinfo("保存するものがありません", "先に文字起こしを実行してください。")
            return
        outdir = filedialog.askdirectory(title="保存先フォルダを選択")
        if not outdir:
            return
        # 画面上で編集された内容を保存する
        self.result.transcript = self.raw_text.get("1.0", tk.END).strip()
        polished = self.polished_text.get("1.0", tk.END).strip()
        self.result.polished = "" if polished.startswith("（推敲版は") else polished
        raw_path, polished_path = write_outputs(self.result, outdir)
        saved = "\n".join(str(p) for p in (raw_path, polished_path) if p)
        messagebox.showinfo("保存しました", saved)


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
