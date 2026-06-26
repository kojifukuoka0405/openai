# pptx-vectorizer

PowerPoint (.pptx) 内の **画像から図形（矩形・円・三角形など）を検出し、編集可能なオートシェイプに変換**するツールです。スクリーンショットや図解画像として貼られた図形を、PowerPoint 上で色変更・移動・リサイズできる本来のオブジェクトに戻します。

## 特長

- 画像内の矩形・楕円/円・三角形・多角形を OpenCV で検出
- 検出した図形を、元画像と同じ位置・サイズの PowerPoint オートシェイプとして配置（塗り色・線色も推定）
- 元画像は残す/削除を選択可能
- **CLI** と **GUI**（tkinter）の両方を提供

## インストール

```bash
pip install -r requirements.txt
```

依存: `python-pptx`, `opencv-python-headless`, `numpy`

## 使い方

### CLI

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

### GUI

```bash
python -m pptx_vectorizer.gui
```

ファイルを選んで「変換実行」を押すだけです。

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
