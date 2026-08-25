#!/usr/bin/env python3
"""回答ファイルの自動取り込みウォッチャーを常駐登録する(全OS対応)。

レポートHTMLの「回答をファイルに保存」だけで、以後は
検知 → 適用 → レポート更新 → 必要なら再変換 → ファイル整理 → OS通知
まで人手なしになる。

登録(どのOSでも同じコマンド):
    .venv/bin/python tools/install_watcher.py            # 登録
    .venv/bin/python tools/install_watcher.py --remove   # 解除
    .venv/bin/python tools/install_watcher.py --dir <共有フォルダ>  # 監視先を追加

OSごとの仕組み:
    macOS   : launchd LaunchAgent(フォルダ変化で即時起動)
    Windows : タスクスケジューラ(1分間隔でチェック。pythonwで無音実行)
    Linux   : crontab(1分間隔)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABEL = "com.notebooklm-pdf2pptx.answers-watch"
TASK_NAME = "notebooklm-pdf2pptx-answers-watch"
CRON_MARK = "# notebooklm-pdf2pptx-answers-watch"
SCRIPT = ROOT / "tools" / "answers_autoingest.py"


def venv_python(windowed: bool = False) -> str:
    cands = [ROOT / ".venv" / "bin" / "python"]
    if windowed:
        cands.insert(0, ROOT / ".venv" / "Scripts" / "pythonw.exe")
    cands.append(ROOT / ".venv" / "Scripts" / "python.exe")
    for c in cands:
        if c.is_file():
            return str(c)
    return sys.executable


def downloads_dir() -> Path:
    sys.path.insert(0, str(ROOT / "tools"))
    from answers_ingest import _downloads_dir
    return _downloads_dir()


def resolve_extra(dirs: list[str]) -> list[str]:
    extra = [str(Path(d).expanduser().resolve()) for d in dirs]
    extra += [p for p in os.environ.get("NBLM_ANSWERS_DIRS", "").split(os.pathsep)
              if p.strip()]
    return list(dict.fromkeys(extra))


# ---------------------------------------------------------------- macOS
def install_darwin(remove: bool, extra: list[str]) -> int:
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    if remove:
        subprocess.run(["launchctl", "unload", str(plist_path)],
                       capture_output=True)
        plist_path.unlink(missing_ok=True)
        print(f"解除しました: {plist_path}")
        return 0
    log = ROOT / "runs" / "autoingest.log"
    watch = [str(downloads_dir()), str(ROOT / "inbox")] + extra
    (ROOT / "inbox").mkdir(exist_ok=True)
    watch_xml = "\n".join(f"    <string>{w}</string>" for w in watch)
    # 追加フォルダは常駐側(autoingest)の探索対象にも入れる
    # (launchdにはユーザーシェルの環境変数が届かないためplistへ焼き込む)
    env_xml = ""
    if extra:
        env_xml = ("  <key>EnvironmentVariables</key>\n  <dict>\n"
                   "    <key>NBLM_ANSWERS_DIRS</key>"
                   f"<string>{os.pathsep.join(extra)}</string>\n  </dict>\n")
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{venv_python()}</string>
    <string>{SCRIPT}</string>
  </array>
  <key>WatchPaths</key>
  <array>
{watch_xml}
  </array>
{env_xml}  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist, "utf-8")
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    r = subprocess.run(["launchctl", "load", str(plist_path)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"launchctl load 失敗: {r.stderr.strip()}")
        return 1
    print(f"登録しました: {plist_path}")
    print(f"監視対象: {', '.join(watch)}")
    print(f"ログ: {log}")
    return 0


# ---------------------------------------------------------------- Windows
def install_windows(remove: bool, extra: list[str]) -> int:
    if remove:
        r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
                           capture_output=True, text=True)
        print("解除しました" if r.returncode == 0 else f"解除失敗: {r.stderr.strip()}")
        return 0 if r.returncode == 0 else 1
    (ROOT / "inbox").mkdir(exist_ok=True)
    if extra:
        # タスクはユーザー環境変数を引き継ぐため setx で永続化する
        r = subprocess.run(["setx", "NBLM_ANSWERS_DIRS", os.pathsep.join(extra)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"環境変数の設定に失敗: {r.stderr.strip()}")
    tr = f'"{venv_python(windowed=True)}" "{SCRIPT}"'
    r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", "1",
                        "/TN", TASK_NAME, "/TR", tr],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"タスク登録失敗: {(r.stderr or r.stdout).strip()}")
        print("管理者PowerShellで再実行するか、上記コマンドを手動で登録してください")
        return 1
    print(f"登録しました: タスクスケジューラ「{TASK_NAME}」(1分間隔)")
    print(f"監視対象: {downloads_dir()}, {ROOT / 'inbox'}"
          + (f", {', '.join(extra)}" if extra else ""))
    return 0


# ---------------------------------------------------------------- Linux
def install_linux(remove: bool, extra: list[str]) -> int:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines = [ln for ln in (r.stdout.splitlines() if r.returncode == 0 else [])
             if CRON_MARK not in ln]
    if remove:
        new = "\n".join(lines) + ("\n" if lines else "")
        subprocess.run(["crontab", "-"], input=new, text=True)
        print("解除しました")
        return 0
    (ROOT / "inbox").mkdir(exist_ok=True)
    log = ROOT / "runs" / "autoingest.log"
    env = (f"NBLM_ANSWERS_DIRS={os.pathsep.join(extra)} " if extra else "")
    lines.append(f"* * * * * {env}{venv_python()} {SCRIPT} >> {log} 2>&1 {CRON_MARK}")
    r = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True)
    if r.returncode != 0:
        print("crontab登録に失敗しました")
        return 1
    print(f"登録しました: crontab(1分間隔)。ログ: {log}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--dir", action="append", default=[], metavar="PATH",
                    help="追加の監視フォルダ(繰り返し可)。ブラウザの保存先を"
                         "変えている場合や、別PCとの共有フォルダ"
                         "(Dropbox/OneDrive等)を指定する")
    args = ap.parse_args()
    extra = resolve_extra(args.dir)
    if sys.platform == "darwin":
        return install_darwin(args.remove, extra)
    if sys.platform == "win32":
        return install_windows(args.remove, extra)
    return install_linux(args.remove, extra)


if __name__ == "__main__":
    raise SystemExit(main())
