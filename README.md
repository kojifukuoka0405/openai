このリポジトリには 2 つのツールが入っています。

| ツール | 内容 | 起動 |
|---|---|---|
| [pptx-vectorizer](#pptx-vectorizer) | PPTX 内の画像を編集可能な図形に変換 | `run_gui.py` |
| [audio-transcriber](#audio-transcriber音声文字起こし--推敲) | 音声ファイルを文字起こし＋推敲版を出力 | `run_transcriber.py` |

---

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
4. 「変換して保存」で、新しい PPTX を書き出す

変換方式は 2 通り（チェックボックスで切替）:

- **範囲内の図形をトレース（推奨・既定）**: 囲った範囲の中にある図形（矩形・円・
  三角形など）を検出し、その図形ごとに編集可能なオートシェイプを作成。色は各図形
  の実際の色を採用するため、背景が白くても中身が白塗りになりません。
- **オフ = 範囲を単色矩形に**: 囲った範囲全体を、範囲の代表色で塗った 1 つの編集
  可能な矩形に変換します。

いずれも PowerPoint 上で色変更・移動・リサイズ・文字入力ができます。

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

---

# audio-transcriber（音声文字起こし ＆ 推敲）

音声ファイルをアップロードすると、**そのままの文字起こし**と、**読みやすく推敲した版**の
2 つを出力するアプリです。MP3 / WAV に対応（そのほか m4a・mp4・flac・ogg・webm も可）。

## モデルを選べます（最新料金つき）

**起動のたびに最新の料金を取得**して画面と CLI に表示します。選択肢は
**「既定＝最高品質のモデル」＋「そのとき最も安い 3 種類」**を自動で選抜するので、
価格改定や新モデルが出ても、常にその時点で賢い選択ができます。

| 用途 | 既定（最高品質） | 選択肢に出る安価なモデル（例） |
|---|---|---|
| 文字起こし | `gpt-transcribe` | `gpt-4o-mini-transcribe` / `gpt-4o-transcribe` / `whisper-1` |
| 推敲 | `gpt-5.6-sol` | `gpt-5-nano` / `gpt-4.1-nano` / `gpt-4o-mini` |

参考（2026-08-21 時点の実勢価格）:

| モデル | 料金 |
|---|---|
| `gpt-transcribe` | 音声1時間 約 $0.27（最高精度） |
| `gpt-4o-mini-transcribe` | 音声1時間 約 $0.18（最安） |
| `gpt-5.6-sol` | 100万トークン 入力 $5.00 / 出力 $30.00 |
| `gpt-5-nano` | 100万トークン 入力 $0.05 / 出力 $0.40 |

- 料金は [LiteLLM の公開価格表](https://github.com/BerriAI/litellm) から取得します
  （OpenAI に機械可読な価格 API がないため）。取得できないときは
  「前回取得した価格」→「内蔵の参考価格」の順に切り替え、**どの価格を表示しているかを画面に明記**します
- 選んだモデルと録音の長さから **概算費用**（例: `概算費用: 約 $1.055（文字起こし $0.270 ＋ 推敲 $0.785）`）を
  実行前に表示します
- モデル一覧と最新料金は CLI でも確認できます: `python -m audio_transcriber --list-models`

使えないモデルがあった場合は自動でフォールバックします
（文字起こし: `gpt-4o-transcribe` → `whisper-1` ／ 推敲: `gpt-5.4` → `gpt-5.1` → `gpt-4.1`）。
`--model` / `--polish-model` には一覧にないモデル名も指定できます。

## 出力される 2 つのテキスト

1. **`<ファイル名>_文字起こし.txt`** — 話された通りのテキスト（フィラーや言い直しもそのまま）
2. **`<ファイル名>_推敲版.md`** — 意味を変えずに、次を整えた版
   - フィラー（えー、あのー、um）と無意味な繰り返しの削除
   - 言い直しを最終形に整理
   - 句読点・改行・段落の付与
   - 明らかな誤変換（同音異義語）の修正、表記ゆれの統一

事実・数値・固有名詞の追加や削除、要約はしません（＝内容は変わりません）。

## 準備

```bash
pip install openai
export OPENAI_API_KEY="sk-..."      # Windows: setx OPENAI_API_KEY "sk-..."
```

API キーは https://platform.openai.com/api-keys で発行できます。
GUI ではキー欄に貼り付けて「このPCに保存」にチェックを入れると、次回から入力不要です
（`~/.audio_transcriber/config.json` に本人だけが読める権限で保存）。

## 使い方（GUI・おすすめ）

| OS | 起動方法 |
|---|---|
| Windows | `run_transcriber.bat` をダブルクリック |
| macOS | `run_transcriber.command` をダブルクリック（初回は右クリック→開く） |
| 共通 | ターミナルで `python run_transcriber.py` |

1. 「音声ファイルを開く」で MP3 / WAV を選ぶ
2. 「モデルと料金」欄で文字起こし／推敲のモデルを選ぶ
   （最新料金と、この録音を処理したときの概算費用がその場で更新されます）
3. 必要なら 言語・推敲スタイル・固有名詞 を設定する
4. 「文字起こし開始」を押す（進捗バーが出ます）
5. 「文字起こし」タブと「推敲版」タブに結果が表示される
6. 画面上で直接編集でき、「両方をファイルに保存」で書き出せる

## 使い方（CLI）

```bash
python -m audio_transcriber --list-models              # 最新料金でモデルを比較
python -m audio_transcriber 会議.mp3
python -m audio_transcriber 録音.wav -o 出力 --language ja --style article
python -m audio_transcriber 長時間.mp3 --model gpt-4o-mini-transcribe --polish-model gpt-5-nano
```

主なオプション:

| オプション | 説明 | 既定 |
|---|---|---|
| `-o, --outdir` | 出力先フォルダ | 入力と同じ場所 |
| `--language` | 音声の言語（`ja` など）。指定すると精度と速度が上がる | 自動判定 |
| `--keywords` | 固有名詞・専門用語をカンマ区切りで指定 | なし |
| `--style` | `readable`（読みやすい書き言葉）/ `verbatim`（最小限）/ `article`（記事・議事録風） | `readable` |
| `--instructions` | 推敲への追加指示（自由記述） | なし |
| `--no-polish` | 推敲版を作らない | 作る |
| `--max-seconds` | 1 リクエストあたりの最大音声長（秒） | 900 |
| `--print` | 結果を標準出力にも表示 | しない |
| `--model` / `--polish-model` | 使うモデル（`--list-models` で料金比較） | `gpt-transcribe` / `gpt-5.6-sol` |
| `--list-models` | 選べるモデルと最新料金を表示して終了 | - |
| `--offline-prices` | 料金の取得をスキップ（キャッシュ／参考価格を使う） | 取得する |

## Python API

```python
from audio_transcriber import Options, transcribe_and_polish, write_outputs

result = transcribe_and_polish("会議.mp3", Options(language="ja", style="article"))
print(result.transcript)   # そのままの文字起こし
print(result.polished)     # 推敲版
write_outputs(result, "out")
```

## 長い音声の扱い

OpenAI の音声 API は 1 回につき 25MB までなので、長い録音は自動で分割して送ります。

- **ffmpeg がある場合**: まず 16kHz モノラル MP3 に圧縮するため、1 時間程度の録音でも
  **分割せず 1 回**で送れます（継ぎ目がないので精度が最も高い）
- **ffmpeg が無い場合**: 追加インストール不要で、MP3 は **フレーム境界**で、
  WAV は **無音寄りの位置**で分割します（単語の途中で切れにくい）
- 分割したときは、直前の断片の文末を次のリクエストに文脈として渡すため、
  固有名詞や文体が継ぎ目でぶれにくくなっています

m4a など MP3/WAV 以外の大きなファイルを分割するには ffmpeg が必要です
（`winget install Gyan.FFmpeg` / `brew install ffmpeg` / `sudo apt-get install ffmpeg`）。

## Windows アプリ (.exe) として使う

Python のインストール不要で、ダブルクリック起動できる単一実行ファイルを作れます。

- **自分の Windows でビルド**: `build_audio_windows.bat` を実行 → `dist\audio-transcriber.exe`
- **クラウド (GitHub Actions) でビルド**: **Actions** タブ → **Build Windows App** →
  **Run workflow**。完了後、Artifacts またはリリース「Windows 最新ビルド (.exe)」から
  `audio-transcriber.exe` をダウンロード

## 制限事項

- API 利用料が発生します。表示される費用は**概算**です（音声 1 分 ≒ 日本語 300 文字として見積もり）。実際の請求額は OpenAI のダッシュボードで確認してください
- 料金は公開価格表から取得した参考値です。最終的な価格は OpenAI の公式ページが正です
- 話者の区別（誰が話したか）は既定では付きません。必要なら
  `--model gpt-4o-transcribe-diarize` を指定してください
- 音質が極端に悪い録音や、強い訛り・複数人の同時発話では誤認識が残ります
- 推敲は内容を変えませんが、誤変換の修正は文脈からの推測です。重要な数値や
  固有名詞は元の文字起こし側と突き合わせて確認してください
