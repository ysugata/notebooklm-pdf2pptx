# エージェント手順書(Claude / Codex 共通)

CodexはこのAGENTS.mdを自動で読む(スキル機能は不要)。依頼は3種類:

| 依頼の言葉 | やること |
|---|---|
| 「編集できるパワポにして」 | `.venv/bin/python convert.py "<入力>" -o "<入力名>_editable.pptx" --inpaint auto --work-dir "work_<資料名>"`。入力はPDF/画像/ほぼ画像のPPTX。完了後 `tools/smoke_test.py <出力> --pages-dir <work>/pages` で健全性確認 |
| 「文字化けを直して」 | 変換時に自動修正+自動トリアージ済み。残った要確認分を `tools/garble_prepare.py <出力pptx> --work-dir <work> --out-dir <work>/feedback` でタスク化。**各タスクの crops/<id>_source.png を実際に開いて読み**、(a)画像から確信できる修正は answers に fix、(b)確信は無いが有力な読みは `<out-dir>/suggestions.json` に `{"タスクID": "推測文言"}`(複数段落は「 / 」区切り)で書く — 推測は自動適用されず、人がレポートの「予測を反映」で採否を選ぶ。(c)読めないものは touch しない。最後に `tools/feedback_report.py` でレポート生成 |
| 「修正指示を反映して」 | 以下の「修正指示の反映ワークフロー」手順1〜5 |

## リポジトリの地図

| 場所 | 何があるか |
|---|---|
| `convert.py` / `tools/` | 実行入口(全操作はここのスクリプト経由。直接編集禁止) |
| `notebooklm_pdf2pptx/` | 変換パイプライン本体 |
| `work_<資料名>/` | 資料ごとの変換キャッシュ+修正台帳(消すと蓄積が失われる) |
| `rules/` | 同梱辞書(builtin_*)と現場学習(learned_*、git管理外) |
| `runs/` / `inbox/` | 適用ログ・回答ファイル置き場(git管理外) |

## 禁止事項(データの蓄積を壊さないために)

- **修正済み・変換済みの出力PPTXを再変換しない**(画像の再圧縮で新しい
  崩れ字が生まれ、その資料の修正台帳・確認済み記録も引き継がれない)。
  変換は必ず元ファイルに対して行い、資料ごとに同じ `--work-dir` を使い回す
- `work_*/` を削除しない(修正台帳=人の決定の永続記録が入っている)

## 完了条件と検証(全業務共通)

作業を終える前に、該当する検証を必ず実行し結果を報告に含める:

1. 変換後: `tools/smoke_test.py <出力.pptx>` が「OK」
2. 適用後: feedback_apply の出力が「拒否 0」(拒否>0のまま終了しない)
3. 文字化け/修正指示: タスク件数Nを最初に控え、全Nが
   fix / skip / needs_human / suggestions のいずれかに分類済み
4. レポート(report.html)生成済み、needs_human は全件列挙
5. **実行しなかった検証があれば、その理由を必ず報告に書く**

# 修正指示の反映ワークフロー

> 初回セットアップ(クローン直後に一度だけ):
> `python3 bootstrap.py --with-lama --with-fonts`
> Python自体が無いPCは `setup.bat`(Windows)/`sh setup.sh`(macOS/Linux)が
> Pythonの導入から面倒を見る。
> 以後のコマンドはすべて `.venv/bin/python`(Windowsは `.venv\Scripts\python`)を使う。

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
   - A分類でも、現在の本文が既に正しく読める場合は skip(=修正不要の確認。
     確認済みとして記録され再提示されなくなる)。
   - 画像で確信が持てない文字は推測で埋めず needs_human。
   - `suggested` がある場合は、それが画像と整合するかを確認して採否を決める。

4. **適用**:
   ```
   .venv/bin/python tools/feedback_apply.py "<編集可能.pptx>" \
       feedback_work/tasks.json feedback_work/answers.json -o "<出力.pptx>" \
       --resolve-markers --work-dir <変換時のworkディレクトリ>
   ```
   `--work-dir` により修正は変換キャッシュ(layout.json等)へも書き戻され、
   再変換しても保たれる。
   スキーマ・ハッシュ・段落数・本文不変性を検証してから適用し、
   `runs/feedback_*.jsonl` に全変更を記録する。拒否が出たら理由を読んで
   answers.json を直し、再実行する。

5. **レポート生成(必須)**:
   ```
   .venv/bin/python tools/feedback_report.py feedback_work/tasks.json \
       feedback_work/answers.json --runs-log runs/feedback_<最新>.jsonl \
       -o feedback_work/report.html
   ```
   `--runs-log` は任意(付ければ適用結果のbefore/afterがレポートに載る)。
   needs_human は**全件・省略なく**切り抜き画像つきでレポートに載る。
   依頼者への報告では、要判断の項目を要約で潰さず、レポートのパスと
   件数、および各項目の一行サマリを必ず伝える。回答を得たら手順3〜4を
   繰り返す。

## 検出ルールの調整

注記の慣習(図形タイプ・文言)が違う相手先の場合は `rules/annotations.yaml`
を編集する。コードは触らない。


## 対話式回答モード(Codexのチャットで選びたい場合)

ユーザーが「ここで回答したい」と言ったら、レポートの代わりに:
1. 各タスクの切り抜き画像をOSのビューアで開いて見せる
   (macOS: `open crops/<id>_source.png` / Windows: `start ...`)
2. チャットで番号選択式に質問する(1=予測を反映: <suggested> /
   2=そのまま / 3=消す / 4=画像のまま残す / 5=正しい文言を入力)
3. 回答を answers.json に集約し、通常どおり適用→レポート生成
4. 回答数+保留数 = タスク数N を確認してから終了する

## 回答ファイルの自動取り込み

依頼者がレポートの「回答をファイルに保存」で作った answers_*.txt を
inbox/(または ~/Downloads)に置いたら:
```
.venv/bin/python tools/answers_ingest.py <tasks.json> --apply
```
がハッシュ照合→answers.json化→適用まで自動実行する。
適用後は必ず prepare→report を再生成する(古いtasksはハッシュ不一致で
適用拒否される。残った要判断だけが新しいレポートに載る)。

## 回答ファイルの自動取り込み(常駐ウォッチャー)

`tools/install_watcher.py` を一度実行すると(macOS=launchd即時 / Windows=タスクスケジューラ1分間隔 / Linux=cron1分間隔)、レポートHTMLの
「回答をファイルに保存」だけで以後は全自動になる:
ダウンロード検知 → ハッシュ照合 → (PPTXが更新済みなら再準備+ID引き継ぎ)
→ 適用 → レポート更新 → 背景変更なら再変換 → 回答ファイルを
`inbox/processed/` へ整理 → OS通知。ダウンロード先はOS設定から自動解決(WindowsのOneDriveリダイレクト等も追従)。ブラウザ独自の保存先や共有フォルダは `--dir <パス>` で監視対象に追加できる。手動で回す場合は
`.venv/bin/python tools/answers_autoingest.py` を実行すれば同じ処理が走る。
未登録ハッシュの回答(別資料など)は誤適用防止のため触らず通知のみ。

