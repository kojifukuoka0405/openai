"""PyInstaller でパッケージ化する音声文字起こしアプリのエントリポイント。

`python audio_app.py` でも GUI が起動する。PyInstaller はこのファイルを起点に
スタンドアロンの実行ファイル（audio-transcriber.exe）を生成する。
"""

from audio_transcriber.gui import main

if __name__ == "__main__":
    main()
