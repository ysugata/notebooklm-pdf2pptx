#!/usr/bin/env python3
"""修正セッションの完全レポートを生成する(自己完結HTML)。

目的: 「わからなかったもの(needs_human)」を省略なく全件、切り抜き画像つきで
依頼者に提示すること。エージェントの要約に依存せず、レポート単体で
全タスクの結末(適用/正しいと確認/要人間/拒否)を監査できる。

使い方:
  feedback_report.py feedback_work/tasks.json feedback_work/answers.json \
      --runs-log runs/feedback_XXXX.jsonl -o feedback_work/report.html
"""
from __future__ import annotations

import argparse
import base64
import html
import json
from pathlib import Path


def img_tag(base: Path, rel: str | None) -> str:
    if not rel:
        return ""
    p = base / rel
    if not p.is_file():
        return ""
    b64 = base64.b64encode(p.read_bytes()).decode()
    return (f'<img src="data:image/png;base64,{b64}" '
            'style="max-width:640px;max-height:200px;display:block;'
            'border:1px solid #ccc;border-radius:4px;margin:4px 0">')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tasks", type=Path)
    ap.add_argument("answers", type=Path, nargs="?")
    ap.add_argument("--runs-log", type=Path, default=None)
    ap.add_argument("-o", "--output", type=Path, required=True)
    args = ap.parse_args()

    base = args.tasks.parent
    tasks = json.loads(args.tasks.read_text("utf-8"))["tasks"]
    answers = {}
    if args.answers and args.answers.is_file():
        for a in json.loads(args.answers.read_text("utf-8")).get("answers", []):
            answers[a["id"]] = a
    results = {}
    if args.runs_log and args.runs_log.is_file():
        for line in args.runs_log.read_text("utf-8").splitlines():
            try:
                r = json.loads(line)
                results[r["id"]] = r
            except Exception:
                pass

    buckets = {"needs_human": [], "applied": [], "skip": [], "rejected": [], "open": []}
    for t in tasks:
        aid = t["id"]
        res = results.get(aid, {})
        ans = answers.get(aid, {})
        status = res.get("result") or ans.get("status") or t.get("status") or "open"
        if status in ("needs_human",):
            buckets["needs_human"].append((t, ans, res))
        elif status == "applied":
            buckets["applied"].append((t, ans, res))
        elif status == "skip":
            buckets["skip"].append((t, ans, res))
        elif status == "rejected":
            buckets["rejected"].append((t, ans, res))
        else:
            buckets["open"].append((t, ans, res))

    e = html.escape
    parts = ["""<!DOCTYPE html><html lang="ja"><meta charset="utf-8">
<title>修正セッションレポート</title>
<style>body{font-family:"Hiragino Sans",sans-serif;max-width:900px;margin:24px auto;
padding:0 16px;line-height:1.7;background:#fafafa;color:#222}
h2{border-bottom:2px solid #b8860b;padding-bottom:4px;margin-top:36px}
.card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px 16px;margin:10px 0}
.id{color:#888;font-size:.85em}.q{background:#fff6e0;border-left:4px solid #b8860b;
padding:6px 10px;margin-top:6px}del{color:#b3423a}ins{color:#2e7d4f;text-decoration:none}
summary{cursor:pointer;font-weight:bold}</style>"""]
    parts.append(f"<h1>修正セッションレポート</h1><p>対象: {e(json.loads(args.tasks.read_text('utf-8'))['source'])}</p>")
    c = {k: len(v) for k, v in buckets.items()}
    parts.append(f"<p>適用 {c['applied']} / 正しいと確認 {c['skip']} / "
                 f"<b>要判断 {c['needs_human']}</b> / 拒否 {c['rejected']} / 未処理 {c['open']}</p>")

    parts.append("<h2>要判断(全件・省略なし) — 正しい文言を教えてください</h2>")
    if not buckets["needs_human"]:
        parts.append("<p>なし</p>")
    for t, ans, res in buckets["needs_human"]:
        cur = " / ".join(t["target"]["paragraphs"]) if t.get("target") else "(図形未特定)"
        parts.append(f'<div class="card"><span class="id">{e(t["id"])} — スライド{t["slide"]}</span>'
                     f"<div>種別: {e(t.get('marker_text') or '')}</div>"
                     f"{img_tag(base, t.get('source_crop') or t.get('crop'))}"
                     f"<div>現在のテキスト: <b>{e(cur)}</b></div>"
                     f"{('<div>備考: ' + e(t.get('note','')) + '</div>') if t.get('note') else ''}"
                     '<div class="q">→ 正しい文言: ____________________</div></div>')

    parts.append("<h2>適用済み</h2>")
    for t, ans, res in buckets["applied"]:
        before = " / ".join(res.get("before", []))
        after = " / ".join(res.get("after", []))
        parts.append(f'<div class="card"><span class="id">{e(t["id"])} — スライド{t["slide"]}</span>'
                     f'<div><del>{e(before)}</del><br><ins>→ {e(after)}</ins></div></div>')

    parts.append(f"<details><summary>正しいと確認した行 ({c['skip']}件)</summary><ul>")
    for t, ans, res in buckets["skip"]:
        cur = " / ".join(t["target"]["paragraphs"]) if t.get("target") else ""
        parts.append(f"<li>{e(t['id'])} (p{t['slide']}): {e(cur[:60])}</li>")
    parts.append("</ul></details>")
    if buckets["rejected"]:
        parts.append("<h2>拒否(要調査)</h2><ul>")
        for t, ans, res in buckets["rejected"]:
            parts.append(f"<li>{e(t['id'])}: {e(res.get('reason',''))}</li>")
        parts.append("</ul>")
    parts.append("</html>")
    args.output.write_text("".join(parts), "utf-8")

    print(f"レポート: {args.output}")
    print(f"要判断: {c['needs_human']}件(全件レポートに画像つきで記載)")
    for t, _a, _r in buckets["needs_human"]:
        cur = " / ".join(t["target"]["paragraphs"]) if t.get("target") else "(図形未特定)"
        print(f"  - {t['id']} p{t['slide']}: {cur[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
