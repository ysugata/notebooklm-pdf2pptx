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
    # エージェント(Claude/Codex)が切り抜き画像を見て書いた推測 {id: "文言"}
    sugg: dict = {}
    sp = base / "suggestions.json"
    if sp.is_file():
        try:
            sugg = json.loads(sp.read_text("utf-8"))
        except Exception:
            sugg = {}
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
        elif status in ("applied", "restored", "erased"):
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
summary{cursor:pointer;font-weight:bold}\n.q label{display:block;margin:3px 0;cursor:pointer}\nbutton{cursor:pointer;padding:4px 10px;margin-right:6px}</style>"""]
    parts.append(f"<h1>修正セッションレポート</h1><p>対象: {e(json.loads(args.tasks.read_text('utf-8'))['source'])}</p>")
    c = {k: len(v) for k, v in buckets.items()}
    parts.append(f"<p>適用 {c['applied']} / 正しいと確認 {c['skip']} / "
                 f"<b>要判断 {c['needs_human']}</b> / 拒否 {c['rejected']} / 未処理 {c['open']}</p>")

    parts.append("<h2>要判断(全件・省略なし) — 対応を選んでください</h2>")
    if not buckets["needs_human"]:
        parts.append("<p>なし</p>")
    else:
        parts.append("<p>各項目で対応を選び、最後に「回答をファイルに保存」を押すだけです。"
                     "下の一括ボタンでまとめて選ぶこともできます。"
                     "「保留」のままの項目には何も起きません(後から回答できます)。</p>")
        parts.append('''<div class="card" style="position:sticky;top:0;z-index:5;background:#fff">一括操作:
 <button onclick="bulk('ai')">予測がある項目は予測を採用</button>
 <button onclick="bulk('del')">未選択をすべて「消す」</button>
 <button onclick="bulk('keep')">未選択をすべて「画像のまま」</button>
 <button onclick="bulk('hold')">すべて保留に戻す</button></div>''')
    for t, ans, res in buckets["needs_human"]:
        tid = t["id"]
        cur = " / ".join(t["target"]["paragraphs"]) if t.get("target") else "(図形未特定)"
        s_val = sugg.get(tid)
        if not s_val and t.get("suggested"):
            s_val = " / ".join(t["suggested"])
        ai_row = ""
        if s_val and s_val != cur:
            ai_row = (f'<label><input type="radio" name="c_{e(tid)}" value="ai" '
                      f'data-text="{e(s_val)}"> 予測を反映: <b>{e(s_val)}</b></label>')
        parts.append(f'<div class="card task" data-id="{e(tid)}">'
                     f'<span class="id">{e(tid)} — スライド{t["slide"]}</span>'
                     f"<div>種別: {e(t.get('marker_text') or '')}</div>"
                     f"{img_tag(base, t.get('source_crop') or t.get('crop'))}"
                     f"<div>現在のテキスト: <b>{e(cur)}</b></div>"
                     f"{('<div>備考: ' + e(t.get('note','')) + '</div>') if t.get('note') else ''}"
                     f'<div class="q">{ai_row}'
                     f'<label><input type="radio" name="c_{e(tid)}" value="self"> 自分で入力: '
                     f'<input class="selfin" data-id="{e(tid)}" style="width:60%;padding:3px" '
                     'placeholder="正しい文言"></label>'
                     f'<label><input type="radio" name="c_{e(tid)}" value="keep"> 元の画像のまま残す(復元)</label>'
                     f'<label><input type="radio" name="c_{e(tid)}" value="del"> この文字を消す</label>'
                     f'<label><input type="radio" name="c_{e(tid)}" value="ok"> 今の文字で正しい</label>'
                     f'<label><input type="radio" name="c_{e(tid)}" value="hold" checked> 保留</label>'
                     '</div></div>')
    if buckets["needs_human"]:
        tasks_sha = None
        sha_file = base / "tasks.sha256"
        if sha_file.is_file():
            tasks_sha = sha_file.read_text("utf-8").strip()
        parts.append(f'''<div class="card">
 <button onclick="collect(false)"
 style="font-size:1.05em;padding:8px 20px;cursor:pointer">回答をまとめてコピー</button>
 <button onclick="collect(true)"
 style="font-size:1.05em;padding:8px 20px;cursor:pointer;margin-left:8px">回答をファイルに保存</button>
 <span id="copied" style="color:#2e7d4f;margin-left:8px"></span>
 <textarea id="out" style="width:100%;height:120px;margin-top:8px"
  placeholder="ここに回答テキストが生成されます"></textarea>
 <p style="color:#888;font-size:.85em">保存したファイル(answers_*.txt)は inbox/ や
 共有フォルダに置くだけで、次回の実行時に自動で取り込まれ適用されます。</p></div>
<script>
const TASKS_SHA = {json.dumps(tasks_sha)};
function bulk(kind){{
  document.querySelectorAll(".task").forEach(card=>{{
    const id = card.dataset.id;
    const sel = card.querySelector('input[name="c_'+id+'"]:checked');
    if(kind === 'hold'){{
      card.querySelector('input[value="hold"]').checked = true; return;
    }}
    if(kind === 'ai'){{
      const ai = card.querySelector('input[value="ai"]');
      if(ai) ai.checked = true; return;
    }}
    if(sel && sel.value === 'hold'){{
      const target = card.querySelector('input[value="'+(kind==='del'?'del':'keep')+'"]');
      if(target) target.checked = true;
    }}
  }});
}}
document.addEventListener("input", ev=>{{
  if(ev.target.classList && ev.target.classList.contains("selfin")){{
    const card = ev.target.closest(".task");
    card.querySelector('input[value="self"]').checked = true;
  }}
}});
function collect(save){{
  const lines = [];
  document.querySelectorAll(".task").forEach(card=>{{
    const id = card.dataset.id;
    const sel = card.querySelector('input[name="c_'+id+'"]:checked');
    if(!sel || sel.value === 'hold') return;
    let v = null;
    if(sel.value === 'ai') v = sel.dataset.text;
    else if(sel.value === 'self') v = (card.querySelector('.selfin').value || '').trim();
    else if(sel.value === 'keep') v = '元に戻す';
    else if(sel.value === 'del') v = '消す';
    else if(sel.value === 'ok') v = 'そのまま';
    if(v) lines.push(id + ": " + v);
  }});
  let text = lines.join("\\n");
  const out = document.getElementById("out");
  out.value = text || "(未記入)";
  if(!text){{
    document.getElementById("copied").textContent =
      "未記入のため何もしていません(空欄の項目は保留のままです)";
    return;
  }}
  if(TASKS_SHA) text = "# tasks_sha256: " + TASKS_SHA + "\\n" + text;
  if(save){{
    const blob = new Blob([text], {{type:"text/plain"}});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "answers_" + (TASKS_SHA ? TASKS_SHA.slice(0,8) : "session") + ".txt";
    a.click();
    document.getElementById("copied").textContent =
      "保存しました。自動取り込みが有効なら数十秒で適用され、OS通知が届きます";
  }} else {{
    out.select();
    try{{ navigator.clipboard.writeText(text);
         document.getElementById("copied").textContent = "コピーしました"; }}
    catch(e){{ document.execCommand("copy");
         document.getElementById("copied").textContent = "選択済み(Cmd+Cでコピー)"; }}
  }}
}}
</script>''')

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
