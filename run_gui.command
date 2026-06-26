#!/usr/bin/env bash
# macOS/Linux 用ランチャー。ダブルクリック（macOS）または ./run_gui.command で起動。
cd "$(dirname "$0")" || exit 1
exec python3 run_gui.py
