#!/usr/bin/env python3
"""回答ファイルの全自動取り込み(フォルダ監視から起動される1回実行型)。

レポートHTMLの「回答をファイルに保存」で作られた answers_*.txt を
Downloads / inbox から発見し、人手なしで適用まで進める:

  1. 回答ファイルのハッシュを runs/tasks_registry.jsonl で照合
  2. タスクセットが最新ならそのまま ingest→apply
  3. 適用済みPPTXでハッシュが変わっていたら(よくある)、garble系は
     自動で prepare をやり直し、タスクIDベースで回答を引き継いで適用
     (本文の不変性はapply側が検証するので安全)
  4. 背景に触れる適用(元に戻す/領域消去)があれば再変換まで実行
  5. レポートを更新し、回答ファイルを inbox/processed/ へ整理
  6. 各段階の結果をOS通知で知らせる(macOS)

登録が無いハッシュのファイルは触らず通知のみ(別資料の回答を
誤適用しないため)。手動実行も可: .venv/bin/python tools/answers_autoingest.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from answers_ingest import _scan_dirs  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")
if not Path(PY).is_file():
    PY = sys.executable
LOCK = ROOT / "runs" / "autoingest.lock"


def notify(message: str) -> None:
    print(message, flush=True)
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["osascript", "-e",
                 'display notification "{}" with title "スライド変換: 回答取り込み"'
                 .format(message.replace('"', "'"))],
                timeout=10)
        except Exception:
            pass


def run(cmd: list[str]) -> tuple[int, str]:
    # launchd起動時のcwdはリポジトリ外のため、常にリポジトリ直下で実行する
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    return r.returncode, (r.stdout + r.stderr)


def resolve_dir(p: str) -> Path:
    """レジストリ/メタ内の相対パス(例: work)をリポジトリ基準で解決する。"""
    path = Path(p)
    return path if path.is_absolute() else ROOT / path


def load_registry() -> dict[str, dict]:
    reg_path = ROOT / "runs" / "tasks_registry.jsonl"
    reg: dict[str, dict] = {}
    if reg_path.is_file():
        for line in reg_path.read_text("utf-8").splitlines():
            try:
                e = json.loads(line)
                reg[e["sha"]] = e  # 後勝ち(最新)
            except Exception:
                continue
    return reg


def file_sha_header(path: Path) -> str | None:
    try:
        head = path.read_text("utf-8").splitlines()[0]
    except Exception:
        return None
    if "tasks_sha256:" in head:
        return head.split("tasks_sha256:", 1)[1].strip()
    return None


def newest_runs_log() -> Path | None:
    logs = sorted((ROOT / "runs").glob("feedback_*.jsonl"),
                  key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def needs_reconvert(runs_log: Path | None) -> bool:
    if runs_log is None or not runs_log.is_file():
        return False
    for line in runs_log.read_text("utf-8").splitlines():
        try:
            if json.loads(line).get("result") in ("restored", "erased"):
                return True
        except Exception:
            continue
    return False


def process(txt: Path, reg: dict[str, dict]) -> None:
    import hashlib
    sha = file_sha_header(txt)
    if sha is None:
        notify(f"{txt.name}: ハッシュ行が無く照合できません(手動確認してください)")
        return
    entry = reg.get(sha)
    if entry is None:
        notify(f"{txt.name}: 未登録のタスクセットです(別資料の回答?)。未処理のまま残します")
        return
    tasks = Path(entry["tasks"])
    source = Path(entry["source"])
    stale = True
    if tasks.is_file() and source.is_file():
        meta = json.loads(tasks.read_text("utf-8"))
        stale = (hashlib.sha256(source.read_bytes()).hexdigest()
                 != meta.get("source_sha256"))
    if stale:
        # 適用等でPPTXが更新済み → garble系はprepareをやり直してID引き継ぎ
        if entry.get("kind") != "garble" or not entry.get("work_dir"):
            notify(f"{txt.name}: タスクが古く自動再準備に対応していません"
                   "(修正指示系)。エージェント実行時に取り込まれます")
            return
        wd = resolve_dir(entry["work_dir"])
        out_dir = wd / "feedback"
        code, out = run([PY, str(ROOT / "tools" / "garble_prepare.py"),
                         str(source), "--work-dir", str(wd),
                         "--out-dir", str(out_dir)])
        if code != 0:
            notify(f"{txt.name}: タスク再準備に失敗しました")
            return
        tasks = out_dir / "tasks.json"
        reg.update(load_registry())  # 再準備で登録された新セットを反映

    cmd = [PY, str(ROOT / "tools" / "answers_ingest.py"), str(tasks),
           str(txt), "--apply", "--archive"]
    if stale:
        cmd.append("--allow-stale")
    code, out = run(cmd)
    if code != 0:
        tail = out.strip().splitlines()[-1] if out.strip() else ""
        notify(f"{txt.name}: 適用できませんでした ({tail[:60]})")
        return
    summary = next((ln for ln in out.splitlines() if ln.startswith("適用")), "適用完了")
    notify(f"{txt.name}: {summary}")

    runs_log = newest_runs_log()
    # レポート更新(開いているreport.htmlの再読み込みで最新状態が見える)
    answers_json = tasks.parent / "answers.json"
    run([PY, str(ROOT / "tools" / "feedback_report.py"), str(tasks),
         str(answers_json)]
        + (["--runs-log", str(runs_log)] if runs_log else [])
        + ["-o", str(tasks.parent / "report.html")])

    if needs_reconvert(runs_log):
        meta_p = resolve_dir(entry["work_dir"]) / "convert_meta.json"
        if meta_p.is_file():
            cm = json.loads(meta_p.read_text("utf-8"))
            notify("背景を変更したため再変換を開始します(数分かかります)")
            code, out = run([PY, str(ROOT / "convert.py"), cm["input"],
                             "-o", cm["output"],
                             "--work-dir", str(resolve_dir(entry["work_dir"]))]
                            + cm.get("argv", []))
            notify("再変換が完了しました" if code == 0 else "再変換に失敗しました")
        else:
            notify("背景を変更しました。次回の変換で出力へ反映されます")


def main() -> int:
    # 多重起動防止(launchdはDownloadsの変化ごとに起動するため)
    if LOCK.is_file():
        try:
            pid = int(LOCK.read_text())
            os.kill(pid, 0)
            return 0  # 実行中
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()))
    try:
        reg = load_registry()
        files = []
        for d in _scan_dirs():
            if d.is_dir():
                files += [p for p in d.glob("answers_*.txt")
                          if "processed" not in p.parts]
        for txt in sorted(files, key=lambda p: p.stat().st_mtime):
            process(txt, reg)
    finally:
        LOCK.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
