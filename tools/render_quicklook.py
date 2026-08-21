"""QuickLook (Apple純正エンジン) でページ毎にPPTXをレンダリングする。

LibreOfficeはmacOSで一部の和文フォント(BIZ UDPGothic等)をYu Gothicへ
勝手に代替するため、QA用途にはApple系エンジンの方が正確。
qlmanageは先頭スライドのみ描画するので、work/pagesのレイアウトから
単一スライドPPTXをページ毎に作って描画する。

使い方:
  .venv/bin/python tools/render_quicklook.py --out-dir work/renders_ql
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

from notebooklm_pdf2pptx.config import Settings
from notebooklm_pdf2pptx.fontlib import FontLibrary
from notebooklm_pdf2pptx.pptx_writer import build_presentation


def render_page(record: dict, pages_dir: Path, out_png: Path,
                settings: Settings, library: FontLibrary, width: int) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pptx = tmp_path / f"page_{record['number']:03d}.pptx"
        build_presentation([record], pages_dir, pptx, settings, library)
        result = subprocess.run(
            ["qlmanage", "-t", "-s", str(width), "-o", str(tmp_path), str(pptx)],
            capture_output=True, timeout=120,
        )
        thumb = tmp_path / (pptx.name + ".png")
        if result.returncode != 0 or not thumb.is_file():
            return False
        image = cv2.imread(str(thumb))
        if image is None:
            return False
        out_png.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_png), image)
        return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages-dir", type=Path, default=Path("work/pages"))
    parser.add_argument("--out-dir", type=Path, default=Path("work/renders_ql"))
    parser.add_argument("--width", type=int, default=1376)
    args = parser.parse_args()

    settings = Settings()
    library = FontLibrary(extra_dirs=[settings.fonts_dir])
    page_dirs = sorted(p for p in args.pages_dir.iterdir() if p.is_dir())
    n_done = 0
    for page_dir in page_dirs:
        marker = page_dir / "done.json"
        if not marker.is_file():
            continue
        record = json.loads(marker.read_text(encoding="utf-8"))
        out_png = args.out_dir / f"render_{record['number']:03d}.png"
        if render_page(record, args.pages_dir, out_png, settings, library, args.width):
            n_done += 1
            print(out_png)
        else:
            print(f"page {record['number']}: QuickLookレンダリング失敗", file=sys.stderr)
    print(f"rendered {n_done} pages -> {args.out_dir}")


if __name__ == "__main__":
    main()
