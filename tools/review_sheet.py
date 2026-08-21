"""低信頼OCR行の確認シート: 元画像に低信頼行の枠と認識文字列を注記する。

使い方:
  .venv/bin/python tools/review_sheet.py --out work/review
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont


def annotate(page_dir: Path, out_dir: Path) -> int:
    layout = json.loads((page_dir / "layout.json").read_text(encoding="utf-8"))
    review = layout.get("review", [])
    if not review:
        return 0
    image = cv2.imread(str(page_dir / "source.png"))
    pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)).convert("RGB")
    draw = ImageDraw.Draw(pil)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc", 16)
    except Exception:
        font = ImageFont.load_default()
    for item in review:
        x0, y0, x1, y1 = item["bbox"]
        draw.rectangle((x0 - 2, y0 - 2, x1 + 2, y1 + 2), outline=(255, 40, 40), width=2)
        label = f"{item['confidence']:.2f} {item['text']}"
        ty = y0 - 22 if y0 > 26 else y1 + 4
        pad = 3
        bbox = draw.textbbox((x0, ty), label, font=font)
        draw.rectangle((bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad),
                       fill=(255, 40, 40))
        draw.text((x0, ty), label, font=font, fill=(255, 255, 255))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"review_{page_dir.name}.png"
    pil.save(out_path)
    print(f"{out_path} ({len(review)}件)")
    return len(review)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages-dir", type=Path, default=Path("work/pages"))
    parser.add_argument("--out", type=Path, default=Path("work/review"))
    args = parser.parse_args()
    total = 0
    for page_dir in sorted(p for p in args.pages_dir.iterdir() if p.is_dir()):
        if (page_dir / "layout.json").is_file():
            total += annotate(page_dir, args.out)
    print(f"合計 {total} 件の要確認行")


if __name__ == "__main__":
    main()
