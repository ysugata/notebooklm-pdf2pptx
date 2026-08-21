# notebooklm-pdf2pptx — NotebookLMのPDFを編集可能なPPTXへ

NotebookLM等が生成する「画像として焼き込まれたスライド」のPDFや画像を、
デザイン(背景・図形・グロー・矢印)をそのまま保ちながら、
文字だけをPowerPointネイティブの編集可能テキストへ置き換えるツール。

> 本ツールは非公式のコミュニティツールであり、GoogleおよびNotebookLMとは
> 一切関係ありません。NotebookLMはGoogle LLCの商標です。

- 生成AIによる手調整なし。全て計測とコードで解く決定論パイプライン
  (OCRと背景修復はローカル推論のみ。クラウド不要・オフライン動作)
- 100ページ級に対応(ページ毎キャッシュ+レジューム、逐次処理でメモリ一定)
- 資料固有の座標・補正はコードに持たない(どのデザインでも同じ処理)

技術の全体像は **[docs/architecture.html](docs/architecture.html)**(図解・ブラウザで開く)と
[docs/DESIGN.md](docs/DESIGN.md)(設計判断の記録)を参照。

## セットアップ

```bash
python3 bootstrap.py --with-lama --with-fonts
```

- Python 3.10〜3.12 推奨。Mac (Apple Silicon/Intel) / Windows / WSL2。
- `--with-lama`: 複雑背景(写真・グロー)の高品質テキスト除去 (LaMa/IOPaint、ローカル実行)。
  省略すると古典インペイントで代替(品質は落ちるが依存が軽い)。
- `--with-fonts`: 推奨フォント6族 (Noto Sans JP / Oswald / Roboto Condensed / Roboto Mono / Rounded Mplus 1c / BIZ UDPGothic) を `fonts/` へ。全てOFLまたはWindows標準。
  NotebookLM系スライドのタイポグラフィに近く、照合精度が大きく上がる。
  **PPTXを開く側のPCにも同じフォントのインストールを推奨**(Google Fontsで無償)。

## 使い方

```bash
# PDF全ページ
.venv/bin/python convert.py "input.pdf" -o output.pptx

# ページ指定 (カンマ・範囲)
.venv/bin/python convert.py "input.pdf" --pages 1,2,5 -o preview.pptx
.venv/bin/python convert.py "input.pdf" --pages 1-50 -o part1.pptx

# 画像ディレクトリ (NotebookLMから画像で出力した場合)
.venv/bin/python convert.py slides_dir/ -o output.pptx

# 「ほぼ画像」のPPTX (スライド画像を貼っただけのデッキを編集可能化)
# ネイティブのテキスト・図形はそのまま持ち越し、画像部分だけ分解する
.venv/bin/python convert.py "input.pptx" -o editable.pptx

# LaMaを使わない高速モード
.venv/bin/python convert.py "input.pdf" --inpaint classic -o output.pptx
```

### 出力・検証

- `output.pptx` — 背景画像 + 編集可能テキスト(16:9)
- `work/report.json` — ページ毎のSSIMスコアと低信頼OCR行(要確認箇所)の一覧
- `work/pages/NNN/` — ページ毎の中間物:
  - `source.png` 元画像 / `background.png` 文字除去済み背景
  - `layout.json` 解決済みレイアウト(フォント・サイズ・字間・色・座標)
  - `qa_render.png` 自己検証レンダリング / `qa_side_by_side.jpg` 元との比較
- レンダリング検証:
  ```bash
  # macOS推奨: QuickLook(Apple純正エンジン)でページ毎に描画
  .venv/bin/python tools/render_quicklook.py --out-dir work/renders_ql
  .venv/bin/python tools/compare.py --renders work/renders_ql   # 元画像との見開き比較
  .venv/bin/python tools/review_sheet.py                        # 低信頼OCR行の注記付き画像
  # LibreOffice/Keynote経由 (注意: LOはBIZ UDPGothic等をYu Gothicへ勝手に代替する)
  .venv/bin/python tools/render_pptx.py output.pptx --out-dir work/renders
  ```

同じ入力・設定での再実行はページ毎キャッシュにより高速化される。
入力や設定を変えると自動で作り直す。

## 仕組み (docs/DESIGN.md に詳細)

```
acquire → ocr → measure → solve → group → remove → compose → qa
```

1. **acquire**: PDFページが「全面1枚画像」なら埋め込み画像をネイティブ解像度で抽出
   (再サンプリングなし)。PowerPointで追記されたテキスト/小画像はPDFから正確な
   フォント・サイズ・色を取り、OCR・修復なしで再配置する。
2. **ocr**: RapidOCR (onnxruntime) + PP-OCRv6 japan。行単位検出、単語ボックスで
   スペース復元。低信頼行は `report.json` にフラグ。
3. **measure**: 行ごとにインクマスク・色・縦グラデーション・ストローク幅を局所計測。
4. **solve**: 候補フォントで同文字列を実描画して照合し、フォント・サイズ・字間(spc)・
   太さ・ベースライン座標を決定論的に解く。
5. **group**: 等ピッチ・同スタイルの行を段落へ。行間はspcPtsで正確指定。整列自動判定。
6. **remove**: 適応式テキスト除去 — フラット背景=決定論フィル /
   グロー付き=LaMa(矩形マスク) / テクスチャ上=LaMa(インク膨張マスクのみ、
   周囲のデザインを保護)。
7. **compose**: a:latin+a:ea、字間、正確な行間、グラデーション文字(gradFill)、
   カーニング無効化(照合と一致させるため)でPPTXへ。
8. **qa**: 同じフォントファイルで自己レンダリングし、元画像とのSSIM・差分を出力。

## GPU利用

処理時間の大半はLaMa(背景のテキスト除去)で、ここはGPUで大幅に速くなる。

- **Windows / Linux + NVIDIA**: `python bootstrap.py --with-lama` がnvidia-smiを検出すると
  CUDA版PyTorchを自動インストールし、実行時に自動でGPUを使う(強制は `--cuda` / `--no-cuda`)
- **Apple Silicon Mac**: 標準でMPS(GPU)を自動利用
- OCR・照合・PPTX生成はCPU処理(元々1〜3秒/ページで律速ではない)
- `--inpaint classic` はGPU不要(全てCPUで高速、品質は落ちる)

## 実測パフォーマンス (Apple Silicon Mac)

- 5ページ(LaMa修復込み): 約2.5分 (初回はモデルDLが加わる)
- 100ページ(classic修復): 約3分 (1.8秒/ページ)、メモリ一定(ピーク約1.2GB)
- OCR+計測+解決のみ: 約2〜5秒/ページ
- 検証SSIM (対元画像): 0.86〜0.96 (2資料・計14ページで検証)

## NotebookLM純正PPTXエクスポートとの関係

NotebookLM (Gemini Notebook) は2026年2月から純正PPTXエクスポートに対応していますが、
これは**AIがレイアウトを再生成する方式**で、アプリ内で見たスライド画像とは
見た目が変わります(フォント置換・レイアウト差異)。
「生成された画像の見た目をそのまま編集可能にする」のが本ツールの役割です。
まず純正エクスポートを試し、見た目が崩れる場合に本ツールを使う運用を推奨します。

## 制約・注意

- 元画像内でAI生成特有の崩れ字(疑似漢字)はそのまま認識されるか誤読になる。
  低信頼行として `report.json` に列挙されるので、PowerPoint上で文字列だけ直せばよい
  (位置・スタイルは合っている)。
- フォントは「最も近いインストール済みフォント」への置換。字間(spc)で幅を
  合わせるため行の位置・長さは保たれるが、字形そのものは近似。
- 背景修復のLaMaはローカルMLモデル(決定論: 同入力→同出力)。
  `--inpaint classic` で完全に古典手法へ切り替え可能。
