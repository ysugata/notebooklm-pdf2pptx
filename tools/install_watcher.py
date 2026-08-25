#!/usr/bin/env python3
"""回答ファイルの自動取り込みウォッチャーを常駐登録する。

レポートで「回答をファイルに保存」を押すと、ダウンロードフォルダへの
ファイル追加をOSが検知して tools/answers_autoingest.py が自動実行される
(適用→レポート更新→必要なら再変換→ファイル整理→OS通知まで人手なし)。

macOS: launchd のLaunchAgent(WatchPaths)として登録。
    .venv/bin/python tools/install_watcher.py            # 登録
    .venv/bin/python tools/install_watcher.py --remove   # 解除
Windows: タスクスケジューラ登録用の案内を表示する。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABEL = "com.notebooklm-pdf2pptx.answers-watch"


def downloads_dir() -> Path:
    sys.path.insert(0, str(ROOT / "tools"))
    from answers_ingest import _downloads_dir
    return _downloads_dir()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--dir", action="append", default=[], metavar="PATH",
                    help="追加の監視フォルダ(繰り返し可)。ブラウザの保存先を"
                         "変えている場合や、別PCからの共有フォルダ"
                         "(Dropbox/OneDrive等)を指定する")
    args = ap.parse_args()

    if sys.platform != "darwin":
        py = ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32"
                               else "bin/python")
        print("このOSでは自動登録は未対応です。以下で定期実行を登録してください:")
        if sys.platform == "win32":
            print('  schtasks /Create /SC MINUTE /MO 1 /TN "nblm-answers" '
                  f'/TR "\"{py}\" \"{ROOT / "tools" / "answers_autoingest.py"}\""')
        else:
            print(f"  crontab: * * * * * {py} {ROOT}/tools/answers_autoingest.py")
        return 0

    plist_path = (Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist")
    if args.remove:
        subprocess.run(["launchctl", "unload", str(plist_path)],
                       capture_output=True)
        plist_path.unlink(missing_ok=True)
        print(f"解除しました: {plist_path}")
        return 0

    py = ROOT / ".venv" / "bin" / "python"
    log = ROOT / "runs" / "autoingest.log"
    import os
    extra = [str(Path(d).expanduser().resolve()) for d in args.dir]
    extra += [p for p in os.environ.get("NBLM_ANSWERS_DIRS", "").split(os.pathsep)
              if p.strip()]
    extra = list(dict.fromkeys(extra))
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
    <string>{py}</string>
    <string>{ROOT / 'tools' / 'answers_autoingest.py'}</string>
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


if __name__ == "__main__":
    raise SystemExit(main())
