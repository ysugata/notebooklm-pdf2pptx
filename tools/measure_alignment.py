"""要素単位のズレ計測: 元画像とレンダリング結果のインク位置を要素ごとに比較する。

各テキスト行について、layout.jsonの期待位置の近傍で
元画像とレンダ画像のインクbboxをそれぞれ実測し、
  dx_left  = レンダ左端 - 元左端
  dy_top   = レンダ上端 - 元上端
  dy_bot   = レンダ下端 - 元下端
  dw, dh   = 幅・高さの差
を出力する。系統誤差(全体が同方向にずれる)と個別誤差を分離できる。

使い方:
  .venv/bin/python tools/measure_alignment.py --renders work/renders
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean, median

import cv2
import numpy as np


def ink_bbox_in_region(image_gray: np.ndarray, x0: float, y0: float, x1: float, y1: float,
                       pad: float = 6.0):
    h, w = image_gray.shape[:2]
    rx0 = max(0, int(x0 - pad))
    ry0 = max(0, int(y0 - pad))
    rx1 = min(w, int(round(x1 + pad)))
    ry1 = min(h, int(round(y1 + pad)))
    if rx1 - rx0 < 2 or ry1 - ry0 < 2:
        return None
    crop = image_gray[ry0:ry1, rx0:rx1]
    _t, binary = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if np.count_nonzero(binary) > binary.size * 0.5:
        binary = cv2.bitwise_not(binary)
    ys, xs = np.nonzero(binary)
    if len(xs) < 8:
        return None
    return (rx0 + xs.min(), ry0 + ys.min(), rx0 + xs.max(), ry0 + ys.max())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages-dir", type=Path, default=Path("work/pages"))
    parser.add_argument("--renders", type=Path, default=Path("work/renders"))
    parser.add_argument("--min-height", type=float, default=18.0,
                        help="この高さ未満の要素は計測ノイズが大きいので除外")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    all_dx, all_dy_top, all_dy_bot, all_dw, all_dh = [], [], [], [], []
    per_page_rows = []
    page_dirs = sorted(p for p in args.pages_dir.iterdir() if p.is_dir())
    for index, page_dir in enumerate(page_dirs, start=1):
        layout_path = page_dir / "layout.json"
        if not layout_path.is_file():
            continue
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        source = cv2.imread(str(page_dir / "source.png"), cv2.IMREAD_GRAYSCALE)
        render_path = args.renders / f"render_{index:03d}.png"
        render = cv2.imread(str(render_path), cv2.IMREAD_GRAYSCALE)
        if source is None or render is None:
            continue
        if render.shape != source.shape:
            render = cv2.resize(render, (source.shape[1], source.shape[0]))

        for block in layout["blocks"]:
            for line in block["lines"]:
                ib = line["ink_bbox"]
                height = ib[3] - ib[1]
                if height < args.min_height:
                    continue
                src = ink_bbox_in_region(source, *ib)
                ren = ink_bbox_in_region(render, *ib, pad=max(8.0, height * 0.4))
                if src is None or ren is None:
                    continue
                dx = ren[0] - src[0]
                dy_top = ren[1] - src[1]
                dy_bot = ren[3] - src[3]
                dw = (ren[2] - ren[0]) - (src[2] - src[0])
                dh = (ren[3] - ren[1]) - (src[3] - src[1])
                all_dx.append(dx)
                all_dy_top.append(dy_top)
                all_dy_bot.append(dy_bot)
                all_dw.append(dw)
                all_dh.append(dh)
                row = (index, line["text"][:26], dx, dy_top, dy_bot, dw, dh,
                       line["size_pt"], block["align"], line["font_family"][:20])
                per_page_rows.append(row)
                if args.verbose:
                    print(f"p{index} dx={dx:+4d} dyT={dy_top:+4d} dyB={dy_bot:+4d} "
                          f"dw={dw:+4d} dh={dh:+4d} {line['size_pt']:5.1f}pt "
                          f"{block['align']:6s} {line['font_family'][:18]:18s} {line['text'][:30]!r}")

    if not all_dx:
        print("計測対象なし")
        return

    def stats(name, values):
        print(f"{name}: mean={mean(values):+6.2f} median={median(values):+6.2f} "
              f"absmax={max(abs(v) for v in values):5.1f} "
              f"|v|>3px: {sum(1 for v in values if abs(v) > 3)}/{len(values)}")

    print(f"\n=== 集計 ({len(all_dx)}要素, 高さ{args.min_height}px以上) ===")
    stats("dx_left ", all_dx)
    stats("dy_top  ", all_dy_top)
    stats("dy_bot  ", all_dy_bot)
    stats("dw(幅)  ", all_dw)
    stats("dh(高さ)", all_dh)

    print("\n=== ずれの大きい順 (上位20) ===")
    worst = sorted(per_page_rows, key=lambda r: -(abs(r[2]) + abs(r[3])))[:20]
    for index, text, dx, dy_top, dy_bot, dw, dh, size, align, font in worst:
        print(f"p{index} dx={dx:+4d} dyT={dy_top:+4d} dw={dw:+4d} dh={dh:+4d} "
              f"{size:5.1f}pt {align:6s} {font:20s} {text!r}")


if __name__ == "__main__":
    main()
