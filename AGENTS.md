# エージェント手順書 — 修正指示の反映ワークフロー

このリポジトリで「注記(修正指示)つきPPTX」を処理するときの手順。
CodexでもClaude Codeでも同じ。**エージェントの自由裁量は answers.json の
中身だけ**であり、ファイル編集・検証・梱包はすべてスクリプトが決定論的に行う。

## 鉄則

- PPTXやスライドXMLを直接編集しない。適用は必ず `tools/feedback_apply.py` 経由。
- 手順を飛ばさない。apply は tasks.json のハッシュ証跡を要求するため、
  prepare を経ずに進むことは物理的にできない。
- 判断できないタスクは無理に埋めず `status: "needs_human"` のまま残す。

## 手順

1. **変換** (入力がまだ編集可能化されていない場合のみ):
   ```
   .venv/bin/python convert.py "<入力.pptx|pdf>" -o "<編集可能.pptx>" --inpaint auto
   ```
   機械的に直せる文字化け(異体字・辞書検証つき形近字)はこの段階で自動修正され、
   `work/report.json` に全件記録される。

2. **タスク抽出**:
   ```
   .venv/bin/python tools/feedback_prepare.py "<編集可能.pptx>" --out-dir feedback_work
   ```
   注記図形(吹き出し等)が検出・分類され、`feedback_work/tasks.json` と
   周辺の切り抜き画像 `feedback_work/crops/` が生成される。
   - A = 文字化け(指し先の文字を読める形に直す。辞書修復の提案が
     `suggested` に入っていることがある)
   - B = 置換指示(注記文中に修正後の文面が含まれる)
   - C = 質問・判断が必要 → **触らない**

3. **回答を書く** (ここだけがエージェントの仕事):
   各タスクについて `crops/` の画像と `target.paragraphs`(現在の本文)を見て、
   `feedback_work/answers.json` を書く:
   ```json
   {"tasks_sha256": "<feedback_work/tasks.sha256 の中身>",
    "answers": [
      {"id": "s007_m21", "status": "fix",
       "paragraphs": ["修正後の段落1", "修正後の段落2"]},
      {"id": "s008_m9", "status": "skip"},
      {"id": "s019_m4", "status": "needs_human"}
    ]}
   ```
   - `paragraphs` は指し先図形の**全段落**を、修正不要の段落も含めて
     元の順序・段落数のまま書く(段落数が違うと適用拒否される)。
   - 画像で確信が持てない文字は推測で埋めず skip。
   - `suggested` がある場合は、それが画像と整合するかを確認して採否を決める。

4. **適用**:
   ```
   .venv/bin/python tools/feedback_apply.py "<編集可能.pptx>" \
       feedback_work/tasks.json feedback_work/answers.json -o "<出力.pptx>" \
       --resolve-markers
   ```
   スキーマ・ハッシュ・段落数・本文不変性を検証してから適用し、
   `runs/feedback_*.jsonl` に全変更を記録する。拒否が出たら理由を読んで
   answers.json を直し、再実行する。

5. **報告**: 適用サマリ(適用/skip/要人間の件数)と、needs_human の一覧を
   依頼者に伝える。needs_human は人間の回答を得てから手順3〜4を繰り返す。

## 検出ルールの調整

注記の慣習(図形タイプ・文言)が違う相手先の場合は `rules/annotations.yaml`
を編集する。コードは触らない。
