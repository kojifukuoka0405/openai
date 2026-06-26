"""PyInstaller でパッケージ化する Windows アプリのエントリポイント。

`python app.py` でも GUI が起動する。PyInstaller はこのファイルを起点に
スタンドアロンの実行ファイル（pptx-vectorizer.exe）を生成する。
"""

from pptx_vectorizer.gui import main

if __name__ == "__main__":
    main()
