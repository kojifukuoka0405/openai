# pptx-vectorizer

PowerPoint (.pptx) 内の画像を **編集可能なオブジェクトに変換**するツールです。

おすすめは **GUI の範囲選択モード**：画像をプレビューし、**ドラッグで囲った範囲だけ**を
編集可能な矩形オートシェイプ（塗りはその範囲の代表色）に変換します。画像全体を自動検出
する旧来の方式と違い、必要な箇所だけを正確に・予測どおりに変換できます。

CLI には従来の自動図形検出モードも残しています。

## 特長

- **GUI 範囲選択モード（推奨）**: ドラッグで囲った範囲だけを編集可能な矩形に変換
  - 複数範囲・複数画像・複数スライドに対応、やり直し/全消去あり
  - 塗り色は選択範囲の代表色（中央値）を自動採用、元画像は残す/削除を選択可能
- **CLI 自動検出モード**: 画像内の矩形・楕円/円・三角形などを OpenCV で一括検出・変換

## インストール

```bash
pip install -r requirements.txt
```

依存: `python-pptx`, `opencv-python-headless`, `numpy`, `Pillow`

## 使い方

### GUI 範囲選択モード（推奨）

1. アプリを起動（下記「ローカルアプリ」参照、または `python -m pptx_vectorizer.gui`）
2. 「PPTX を開く」でファイルを読み込む
3. 表示された画像の上を **ドラッグ** して、編集可能にしたい範囲を囲む（複数可）
   - スライドに複数画像があれば「◀ 前の画像 / 次の画像 ▶」で切り替え
   - 右クリックまたは「直前の範囲を取消」でやり直し、「全消去」で全クリア
4. 「変換して保存」で、囲った範囲が編集可能な矩形オートシェイプになった新しい
   PPTX を書き出す（PowerPoint で色変更・移動・リサイズ・文字入力が可能）

### CLI（自動検出モード）

画像全体から図形を一括検出します。

```bash
python -m pptx_vectorizer 入力.pptx 出力.pptx
```

主なオプション:

| オプション | 説明 | 既定 |
|---|---|---|
| `--remove-original` | 変換後に元画像を削除 | 残す |
| `--min-area-ratio` | 検出図形の最小面積比 | 0.002 |
| `--max-area-ratio` | 検出図形の最大面積比 | 0.98 |
| `--epsilon` | 輪郭近似の許容誤差 | 0.03 |

例:

```bash
python -m pptx_vectorizer deck.pptx deck_vectorized.pptx --remove-original
```

### GUI（ローカルアプリ）

最も簡単な起動方法 — **依存ライブラリの自動インストール付きランチャー**を使います:

| OS | 起動方法 |
|---|---|
| Windows | `run_gui.bat` をダブルクリック |
| macOS | `run_gui.command` をダブルクリック（初回は右クリック→開く） |
| 共通 | ターミナルで `python run_gui.py` |

ランチャーが不足している依存を検出して自動で `pip install` し、GUI を起動します。
`tkinter` が無い場合は OS ごとの追加手順を案内します
（Ubuntu/Debian なら `sudo apt-get install python3-tk`）。

依存を手動で入れて直接起動することもできます:

```bash
python -m pptx_vectorizer.gui
```

ファイルを選んで「変換実行」を押すだけです。

### Windows アプリ (.exe) として使う

インストール不要でダブルクリック起動できる単一実行ファイルを作れます。方法は2つ:

**A. 自分の Windows でビルドする**

リポジトリを取得し、`build_windows.bat` をダブルクリック（または実行）します。
依存と PyInstaller を入れて `dist\pptx-vectorizer.exe` を生成します。
以後はその exe をダブルクリックするだけでアプリが起動します。

**B. クラウド (GitHub Actions) でビルドして DL する**（Windows 不要）

1. GitHub の **Actions** タブ → **Build Windows App** → **Run workflow**
2. 完了後、実行結果の **Artifacts** から `pptx-vectorizer-windows` をダウンロード
3. 中の `pptx-vectorizer.exe` を Windows で起動

`v1.0.0` のようなタグを push すると、自動で Release に exe が添付されます。

### Python API

```python
from pptx_vectorizer import convert_presentation

report = convert_presentation("in.pptx", "out.pptx")
print(report.pictures_processed, report.shapes_created)
```

## 仕組み

1. 各スライドの埋め込み画像（Picture）を取り出す
2. Canny エッジ + 輪郭抽出で図形を検出し、頂点数と円形度で種別を判定
3. ピクセル座標を画像の表示位置・サイズに合わせて EMU へスケール
4. 対応するオートシェイプ（RECTANGLE / OVAL / ISOSCELES_TRIANGLE …）を追加

## 制限事項

- 写真や複雑なイラストではなく、**単純で輪郭のはっきりした図形**に最適化されています
- 曲線・自由形状・重なった図形・グラデーションは正確に再現できません
- 線の太さや回転角は推定しません（必要に応じて手動調整してください）

## テスト

```bash
python -m pytest tests/ -q
```
