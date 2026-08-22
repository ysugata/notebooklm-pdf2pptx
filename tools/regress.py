#!/usr/bin/env python3
"""参照資料の回帰テスト。

複数の参照入力(異なるデザインの資料)を変換し、ページ別SSIMを保存済み
ベースラインと比較する。特定の資料に合わせたチューニングが他のデザインを
劣化させていないか(過適合)をリリース前に検出するためのツール。

使い方:
    # 参照入力の登録 (1行1パス。work/はgit管理外なのでローカル専用)
    printf '%s\n' "/path/a.pdf" "/path/b.pptx" > work/regress_inputs.txt

    # ベースライン記録 (初回、または意図的に基準を更新するとき)
    .venv/bin/python tools/regress.py --record

    # 回帰チェック (ページSSIMがベースラインより tolerance 超低下で失敗)
    .venv/bin/python tools/regress.py

各入力は work/regress/<名前>/ を作業ディレクトリとして変換される
(ページ毎キャッシュが効くので2回目以降は高速)。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "work" / "regress_baseline.json"
INPUTS = ROOT / "work" / "regress_inputs.txt"


def convert(input_path: Path, work_dir: Path, inpaint: str) -> dict:
    out = work_dir / "output.pptx"
    work_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(ROOT / "convert.py"), str(input_path),
         "-o", str(out), "--work-dir", str(work_dir), "--inpaint", inpaint],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"convert failed for {input_path}:\n{result.stdout[-2000:]}"
                           f"\n{result.stderr[-2000:]}")
    report = json.loads((work_dir / "report.json").read_text(encoding="utf-8"))
    return {page: entry["ssim"] for page, entry in report["qa"].items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="*", type=Path,
                        help=f"参照入力 (省略時は {INPUTS} を読む)")
    parser.add_argument("--record", action="store_true",
                        help="現在の結果をベースラインとして保存")
    parser.add_argument("--tolerance", type=float, default=0.010,
                        help="許容するSSIM低下幅 (既定 0.010)")
    parser.add_argument("--inpaint", default="auto",
                        help="修復方式 (auto|classic。classicは高速)")
    args = parser.parse_args()

    inputs = args.inputs
    if not inputs:
        if not INPUTS.is_file():
            print(f"入力リストがありません: {INPUTS}")
            return 2
        inputs = [Path(line.strip()) for line in
                  INPUTS.read_text(encoding="utf-8").splitlines() if line.strip()]

    results: dict[str, dict] = {}
    for path in inputs:
        name = path.stem.replace(" ", "_")
        print(f"=== {name} を変換中 ===", flush=True)
        results[name] = convert(path, ROOT / "work" / "regress" / name, args.inpaint)

    if args.record:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print(f"ベースラインを保存: {BASELINE}")
        return 0

    if not BASELINE.is_file():
        print(f"ベースラインがありません。--record で記録してください: {BASELINE}")
        return 2
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    failures = []
    for name, pages in results.items():
        base = baseline.get(name, {})
        for page, ssim in pages.items():
            if page in base and ssim < base[page] - args.tolerance:
                failures.append((name, page, base[page], ssim))
    if failures:
        print("回帰を検出:")
        for name, page, old, new in failures:
            print(f"  {name} page {page}: SSIM {old:.4f} -> {new:.4f}")
        return 1
    print(f"回帰なし ({sum(len(p) for p in results.values())}ページ、"
          f"許容低下 {args.tolerance})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
