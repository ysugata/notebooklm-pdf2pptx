#!/usr/bin/env python3
"""人間の回答テキスト(answers_*.txt)を取り込み、検証つきで適用する。

レポートHTMLの「回答をファイルに保存」で作られるファイル形式:
    # tasks_sha256: <ハッシュ>
    g027_0: 3.96
    g023_4: そのまま
    g029_3: 消す
    g026_1: 保留

回答の解釈:
    そのまま → skip(修正不要と確認)
    消す     → 全段落を空文字に(アイコン上の断片など)
    保留     → needs_human のまま
    それ以外 → 正しい文言として適用。指し先が複数段落の場合は
               「 / 」区切りで全段落を書く。段落数が合わなければ
               適用せず要判断に残す(fail-safe)

使い方:
    answers_ingest.py <tasks.json>                # inbox/等から自動発見
    answers_ingest.py <tasks.json> <answers.txt>  # ファイル指定
    --apply を付けると feedback_apply まで自動実行する
    (入力PPTX・workディレクトリは tasks.json のメタから解決)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCAN_DIRS = [ROOT / "inbox", Path.home() / "Downloads"]


def find_answer_file(tasks_sha: str) -> Path | None:
    """inbox/Downloads から、このtasksに対応する最新の回答ファイルを探す。"""
    candidates: list[tuple[float, Path]] = []
    for d in SCAN_DIRS:
        if not d.is_dir():
            continue
        for p in d.glob("answers_*.txt"):
            try:
                head = p.read_text("utf-8").splitlines()[0]
            except Exception:
                continue
            if tasks_sha in head:
                candidates.append((p.stat().st_mtime, p))
    if not candidates:
        return None
    return max(candidates)[1]


def parse_answers(text: str, tasks: dict) -> tuple[list[dict], list[str]]:
    answers = []
    problems = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            problems.append(f"解釈不能な行: {line[:40]}")
            continue
        tid, _, value = line.partition(":")
        tid = tid.strip()
        value = value.strip()
        t = tasks.get(tid)
        if t is None:
            problems.append(f"未知のID: {tid}")
            continue
        if value in ("そのまま", "OK", "ok", "現状維持"):
            answers.append({"id": tid, "status": "skip"})
        elif value in ("保留", "わからない", "スキップ"):
            answers.append({"id": tid, "status": "needs_human"})
        elif value in ("元に戻す", "復元", "アイコン"):
            answers.append({"id": tid, "status": "restore"})
        elif value in ("消す", "削除"):
            n = len(t["target"]["paragraphs"]) if t.get("target") else 1
            answers.append({"id": tid, "status": "fix", "paragraphs": [""] * n})
        else:
            target = t.get("target")
            if target is None:
                problems.append(f"{tid}: 指し先図形が無いため文言を適用できません")
                answers.append({"id": tid, "status": "needs_human"})
                continue
            n = len(target["paragraphs"])
            paras = [p.strip() for p in value.split(" / ")] if " / " in value else [value]
            if len(paras) == n:
                answers.append({"id": tid, "status": "fix", "paragraphs": paras})
            elif n == 1:
                answers.append({"id": tid, "status": "fix", "paragraphs": [value]})
            else:
                problems.append(
                    f"{tid}: 段落数不一致 (図形は{n}段落。「 / 」区切りで全段落を書いてください)")
                answers.append({"id": tid, "status": "needs_human"})
    return answers, problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tasks", type=Path)
    ap.add_argument("answers_txt", type=Path, nargs="?")
    ap.add_argument("--allow-stale", action="store_true",
                    help="回答ファイルのハッシュが現在のtasksと違っても、"
                         "タスクIDベースで取り込む(本文不変性はapply側が検証)")
    ap.add_argument("--apply", action="store_true",
                    help="answers.json生成後に feedback_apply まで実行する")
    ap.add_argument("-o", "--output-pptx", type=Path, default=None,
                    help="--apply時の出力先(省略時は入力PPTXを上書き)")
    args = ap.parse_args()

    tasks_body = args.tasks.read_text("utf-8")
    tasks_sha = hashlib.sha256(tasks_body.encode()).hexdigest()
    meta = json.loads(tasks_body)
    tasks = {t["id"]: t for t in meta["tasks"]}

    txt_path = args.answers_txt or find_answer_file(tasks_sha)
    if txt_path is None and args.allow_stale:
        cands = [p for d in SCAN_DIRS if d.is_dir() for p in d.glob("answers_*.txt")]
        txt_path = max(cands, key=lambda p: p.stat().st_mtime) if cands else None
    if txt_path is None:
        print("回答ファイルが見つかりません (inbox/ か ~/Downloads の answers_*.txt)")
        return 2
    text = txt_path.read_text("utf-8")
    head = text.splitlines()[0] if text.splitlines() else ""
    if "tasks_sha256" in head and tasks_sha not in head:
        if not args.allow_stale:
            print(f"適用拒否: {txt_path.name} は別の(古い)タスクセットへの回答です。"
                  "内容を引き継ぐ場合は --allow-stale を付けてください")
            return 1
        print("注意: 古いタスクセットへの回答をIDベースで引き継ぎます"
              "(本文の不変性はapply側で検証されます)")

    answers, problems = parse_answers(text, tasks)
    out_json = args.tasks.parent / "answers.json"
    out_json.write_text(json.dumps(
        {"tasks_sha256": tasks_sha, "answers": answers},
        ensure_ascii=False, indent=1), "utf-8")
    n_fix = sum(1 for a in answers if a["status"] == "fix")
    print(f"取り込み: {txt_path}")
    print(f"answers.json: fix {n_fix} / restore "
          f"{sum(1 for a in answers if a['status'] == 'restore')} / skip "
          f"{sum(1 for a in answers if a['status'] == 'skip')} / 保留 "
          f"{sum(1 for a in answers if a['status'] == 'needs_human')}")
    for p in problems:
        print(f"  注意: {p}")

    if args.apply:
        src = Path(meta["source"])
        out = args.output_pptx or src
        cmd = [sys.executable, str(ROOT / "tools" / "feedback_apply.py"),
               str(src), str(args.tasks), str(out_json), "-o", str(out)]
        if meta.get("work_dir"):
            cmd += ["--work-dir", meta["work_dir"]]
        result = subprocess.run(cmd)
        return result.returncode
    print("次: tools/feedback_apply.py で適用してください(または --apply を付けて再実行)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
