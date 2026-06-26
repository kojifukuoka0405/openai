# PyInstaller スペックファイル。
# ビルド: pyinstaller pptx_vectorizer.spec
# 出力:   dist/pptx-vectorizer.exe（単一の実行ファイル / ウィンドウアプリ）

block_cipher = None

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        "pptx",
        "cv2",
        "numpy",
        "PIL",
        "PIL._tkinter_finder",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="pptx-vectorizer",
    debug=False,
    strip=False,
    upx=True,
    console=False,          # GUI アプリ（コンソール非表示）
    icon=None,
)
