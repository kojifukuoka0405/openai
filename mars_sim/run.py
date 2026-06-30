#!/usr/bin/env python3
"""TERRA ASCENDANT ランチャー。

  python run.py                       # 観測モードのデモ（自律進行を鑑賞）
  python run.py --mode campaign --nation japan
  python run.py --mode observer --nation usa --fate harsh --turns 40

詳細な引数は `python run.py --help`。
"""

from terra_sim.cli import main

if __name__ == "__main__":
    main()
