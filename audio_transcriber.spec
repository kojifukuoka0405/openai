# PyInstaller スペックファイル（音声文字起こしアプリ）。
# ビルド: pyinstaller audio_transcriber.spec
# 出力:   dist/audio-transcriber.exe（単一の実行ファイル / ウィンドウアプリ）

from PyInstaller.utils.hooks import collect_all

# openai SDK は動的 import とデータファイルを含むため、まとめて取り込む
openai_datas, openai_binaries, openai_hiddenimports = collect_all("openai")

block_cipher = None

a = Analysis(
    ["audio_app.py"],
    pathex=[],
    binaries=openai_binaries,
    datas=openai_datas,
    hiddenimports=openai_hiddenimports + [
        "audio_transcriber",
        "audio_transcriber.gui",
        "httpx",
        "httpcore",
        "certifi",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["cv2", "pptx", "matplotlib"],   # 音声アプリでは使わない大きな依存
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="audio-transcriber",
    debug=False,
    strip=False,
    upx=True,
    console=False,          # GUI アプリ（コンソール非表示）
    icon=None,
)
