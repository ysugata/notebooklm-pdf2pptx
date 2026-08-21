"""元画像とレンダリング結果の見開き比較シートを作る。

使い方:
  .venv/bin/python tools/compare.py --renders work/renders --out work/compare
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages-dir", type=Path, default=Path("work/pages"))
    parser.add_argument("--renders", type=Path, default=Path("work/renders"))
    parser.add_argument("--out", type=Path, default=Path("work/compare"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    page_dirs = sorted(p for p in args.pages_dir.iterdir() if p.is_dir())
    for index, page_dir in enumerate(page_dirs, start=1):
        source = cv2.imread(str(page_dir / "source.png"))
        render_path = args.renders / f"render_{index:03d}.png"
        render = cv2.imread(str(render_path))
        if source is None or render is None:
            continue
        render = cv2.resize(render, (source.shape[1], source.shape[0]))
        divider = np.full((source.shape[0], 4, 3), 255, np.uint8)
        side = np.hstack([source, divider, render])
        label = f"page {page_dir.name}: left=original right=pptx render"
        cv2.putText(side, label, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (0, 255, 0), 2, cv2.LINE_AA)
        out_path = args.out / f"compare_{page_dir.name}.jpg"
        cv2.imwrite(str(out_path), side, [cv2.IMWRITE_JPEG_QUALITY, 88])
        print(out_path)


if __name__ == "__main__":
    main()
