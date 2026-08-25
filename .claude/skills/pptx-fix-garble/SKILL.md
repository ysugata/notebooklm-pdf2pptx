---
name: pptx-fix-garble
description: ユーザーが「文字化けを直して」「崩れた文字を修正して」と頼んだとき、または変換後のPPTXに要確認行が残っているときに使う。除去前の原本画像の切り抜きを見て、AI崩れ字の誤読を修正する。注記が無くても使える。
---

# 文字化け修正スキル

決定論の辞書修復で直る分は変換時に済んでいる。残った要確認行を、
**除去前の原本画像の切り抜き**を見て人間の代わりに転記・修正する。

## 手順

1. タスクを抽出する(決定論):
   ```
   .venv/bin/python tools/garble_prepare.py "<編集可能.pptx>" \
       --work-dir <変換時のworkディレクトリ> --out-dir feedback_work
   ```
   ✓ 確認: 出力の「tasks.json: N件」の **Nを控える**。以降、全Nタスクを
   3分類(answers / suggestions / 読めない)のいずれかに入れるまで終えない。

2. 各タスクを判断する(このスキルの本体)。タスクごとに:
   1. `crops/<id>_source.png` を **Readで実際に開いて見る**(画像を見ずに
      テキストだけで判断することを禁止する)
   2. 分類する:
      - 画像の文字が読める → answers に `fix`(正しい文言)
      - 現在の本文が画像と一致して正しい → answers に `skip` 相当(そのまま)
      - 読みに確信は無いが有力な推測がある → `feedback_work/suggestions.json`
        に `{"タスクID": "推測文言"}` を書く(複数段落は「 / 」区切り)。
        推測は自動適用されず、人がレポートの「予測を反映」で採否を選ぶ
      - 画像も文脈も不明 → answers に `needs_human`(創作しない)
   3. `paragraphs` は指し先図形の全段落を元の順序・段落数のまま書く
   ✓ 確認: 処理済み件数を数え、Nに達するまで2を繰り返す。省略・間引きをしない。

3. answers.json を書く(tasks_sha256 は feedback_work/tasks.sha256 の中身):
   ```json
   {"tasks_sha256": "…", "answers": [
     {"id": "g002_7", "status": "fix", "paragraphs": ["介護"]},
     {"id": "g016_0", "status": "needs_human"}]}
   ```
   ✓ 確認: answers + suggestions + 読めない の合計 = N になっている。

4. 適用する(決定論・検証つき):
   ```
   .venv/bin/python tools/feedback_apply.py "<編集可能.pptx>" \
       feedback_work/tasks.json feedback_work/answers.json -o "<出力.pptx>" \
       --work-dir <変換時のworkディレクトリ>
   ```
   ✓ 確認: 出力の「拒否 0」を確認する。拒否>0なら理由を読み、answersを
   直して再実行する(拒否を残したまま次へ進まない)。

5. レポートを生成する(必須):
   ```
   .venv/bin/python tools/feedback_report.py feedback_work/tasks.json \
       feedback_work/answers.json --runs-log runs/feedback_<最新>.jsonl \
       -o feedback_work/report.html
   ```

## 完了条件(全て満たすまで終了しない)

- [ ] 全Nタスクが answers / suggestions / needs_human のいずれかに分類済み
- [ ] feedback_apply の拒否 0
- [ ] report.html 生成済み
- [ ] 報告に needs_human を**全件一行ずつ**列挙(要約で潰さない)

実行できなかった確認があれば、その理由を必ず報告に書く。

## 鉄則

- PPTXを直接編集しない。適用は必ず feedback_apply.py 経由
- タスク数が多い場合は数十件ずつ画像確認→適用を繰り返してよい
  (applyは同一図形への追加適用に対応している)

## 回答ファイルの自動取り込み

依頼者がレポートの「回答をファイルに保存」で作った answers_*.txt を
inbox/(または ~/Downloads)に置いたら:
```
.venv/bin/python tools/answers_ingest.py <tasks.json> --apply
```
がハッシュ照合→answers.json化→適用まで自動実行する。
適用後は必ず prepare→report を再生成する(古いtasksはハッシュ不一致で
適用拒否される。残った要判断だけが新しいレポートに載る)。
