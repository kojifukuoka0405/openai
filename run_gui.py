#!/usr/bin/env python3
"""ローカルで GUI アプリを起動するランチャー。

依存ライブラリが未インストールなら自動で pip install を試みてから、
tkinter GUI を起動する。ダブルクリックまたは `python run_gui.py` で実行。
"""

from __future__ import annotations

import importlib
import subprocess
import sys

# import 名 -> pip パッケージ名
REQUIRED = {
    "pptx": "python-pptx",
    "cv2": "opencv-python-headless",
    "numpy": "numpy",
    "PIL": "Pillow",
}


def ensure_dependencies() -> None:
    missing = []
    for mod, pkg in REQUIRED.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pkg)

    if not missing:
        return

    print(f"不足している依存をインストールします: {', '.join(missing)}")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
    except subprocess.CalledProcessError:
        sys.exit(
            "依存のインストールに失敗しました。手動で次を実行してください:\n"
            f"  {sys.executable} -m pip install {' '.join(missing)}"
        )


def check_tkinter() -> None:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        sys.exit(
            "tkinter が見つかりません。OS の Python に tkinter を追加してください。\n"
            "  - Windows/macOS: python.org の公式インストーラには同梱されています\n"
            "  - Ubuntu/Debian: sudo apt-get install python3-tk\n"
            "  - Fedora: sudo dnf install python3-tkinter"
        )


def main() -> None:
    check_tkinter()
    ensure_dependencies()
    from pptx_vectorizer.gui import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
