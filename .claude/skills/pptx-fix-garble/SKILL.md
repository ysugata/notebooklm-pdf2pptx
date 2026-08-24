---
name: pptx-fix-garble
description: 編集可能化したPPTXに残る文字化け(AI崩れ字の誤読)を、除去前の原本画像を見て修正する。ユーザーが「文字化けを直して」と頼んだとき、注記が無くても使える。
---

# 文字化け修正スキル

決定論の辞書修復で直る分は変換時に済んでいる。ここでは残った要確認行を、
**除去前の原本画像の切り抜き**を見て人間の代わりに転記・修正する。

## 手順

1. タスク抽出(決定論):
   ```
   .venv/bin/python tools/garble_prepare.py "<編集可能.pptx>" \
       --work-dir <変換時のworkディレクトリ> --out-dir feedback_work
   ```
2. 判断(このスキルの本体): 各タスクの `source_crop`(原本画像の切り抜き)を
   **必ずReadで実際に見て**、正しい文字列を決める。
   - 原本の崩れ字が読めるならその文字に直す
   - `suggested` があれば画像と整合するか確認して採否を決める
   - **読めない・確信が持てない場合は推測で埋めず `needs_human`**
   - `paragraphs` は指し先図形の全段落を元の順序・段落数のまま書く
3. answers.json を書く(tasks_sha256 は feedback_work/tasks.sha256 の中身):
   ```json
   {"tasks_sha256": "…", "answers": [
     {"id": "g002_7", "status": "fix", "paragraphs": ["介護"]},
     {"id": "g016_0", "status": "needs_human"}]}
   ```
4. 適用(決定論・検証つき):
   ```
   .venv/bin/python tools/feedback_apply.py "<編集可能.pptx>" \
       feedback_work/tasks.json feedback_work/answers.json -o "<出力.pptx>"
   ```
   拒否が出たら理由を読んで answers を直し再実行。
5. 報告: 直した件数と内容(X→Y)、読めず保留にした件数と場所、
   画像のまま保持で修正対象外だった行の一覧。

## 鉄則

- PPTXを直接編集しない。適用は必ず feedback_apply.py 経由
- タスク数が多い場合は数十件ずつ画像確認→適用を繰り返してよい
  (applyは同一図形への追加適用に対応している)
