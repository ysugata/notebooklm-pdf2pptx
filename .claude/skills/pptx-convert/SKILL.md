---
name: pptx-convert
description: NotebookLM等の画像スライド(PDF/画像/ほぼ画像のPPTX)を、デザインを保ったまま文字が編集できるPPTXに変換する。ユーザーがスライドファイルの変換・編集可能化を頼んだときに使う。
---

# 編集可能化スキル

非エンジニアの依頼者に代わって変換を実行し、結果を分かりやすく報告する。

## 手順

1. 環境確認: `.venv/bin/python` が無ければ初回セットアップを実行:
   `python3 bootstrap.py --with-lama --with-fonts`(数分かかる旨を先に伝える)
2. 変換:
   ```
   .venv/bin/python convert.py "<入力ファイル>" -o "<入力名>_editable.pptx" --inpaint auto
   ```
   - 大きい資料は時間がかかる(目安: 60ページで1時間)。途中で止まっても
     同じコマンドの再実行で続きから再開される
   - 別資料を続けて変換するときは `--work-dir work_<名前>` で分ける
3. 検証: `.venv/bin/python tools/smoke_test.py "<出力.pptx>"` がOKであること
4. 報告(専門用語を避ける):
   - 何枚変換され、いくつの文字が編集可能になったか
   - 自動修正された文字化けの件数(reviewの「文字化け予測修正」)
   - 要確認箇所の件数と、直すなら `/pptx-fix-garble` でできること
   - 出力ファイルの場所

## 注意

- 変換済み文字はクリックすればPowerPointで普通に編集できる
- ごく一部、照合できない装飾文字は画像のまま残る(報告に含める)
