---
name: pptx-apply-feedback
description: 吹き出し等の注記(修正指示)が付いたPPTXを受け取り、指示を解釈して本文へ反映する。「修正指示を反映して」「戻ってきた赤入れを処理して」と頼まれたときに使う。
---

# 修正指示反映スキル

注記(吹き出し・指示文言)を検出・分類し、指示どおりに本文を直す。
詳細な手順契約はリポジトリ直下の AGENTS.md と同一。

## 手順

1. 入力がまだ編集可能化されていなければ先に `/pptx-convert` 相当を実行する
2. タスク抽出(決定論):
   ```
   .venv/bin/python tools/feedback_prepare.py "<編集可能.pptx>" \
       --out-dir feedback_work --work-dir <変換時のworkディレクトリ>
   ```
   - A=文字化け / B=置換指示 / C=質問・判断が必要、に分類される
   - 相手先の注記の慣習が違う場合は rules/annotations.yaml を調整
3. 判断: 各タスクの `crop`(現状)と `source_crop`(除去前の原本)を
   **必ずReadで見て**、A/Bタスクの修正文面を決める。
   - Cタスク(質問系)は**触らず** needs_human のまま残す
   - 確信が持てないAタスクも needs_human
4. **適用前に修正計画の一覧(どこを何から何へ)をユーザーに提示**する。
   了承を得てから適用する(勝手に本文を書き換えない):
   ```
   .venv/bin/python tools/feedback_apply.py "<編集可能.pptx>" \
       feedback_work/tasks.json feedback_work/answers.json \
       -o "<出力_修正版.pptx>" --resolve-markers
   ```
   - 注記は既定で残す(--resolve-markers は処理済みに✔を付けるだけ)。
     消すのはユーザーの指示があった場合のみ
5. 報告: 適用/保留/拒否の件数、needs_human の一覧(質問はそのまま
   依頼者へ返す)、出力ファイルの場所、変更ログ(runs/*.jsonl)の場所

## 鉄則

- 自由裁量は answers.json の中身だけ。PPTX/XMLの直接編集は禁止
- apply はハッシュ証跡・本文不変性を検証する。拒否されたら手順を疑う
