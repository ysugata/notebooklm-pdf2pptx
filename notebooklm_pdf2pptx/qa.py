"""QA: 変換結果の自己検証。

layout.json のスタイル・座標を、PPTXが参照するのと同じフォントファイルで
PIL描画し、元画像との類似度(SSIM)と差分画像を出力する。
レイアウト計算の退行をAIなし・PowerPointなしで検出できる。
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .config import Settings
from .fontlib import FontLibrary, _load_pil_font


def _ssim(a: np.ndarray, b: np.ndarray) -> float:
    """グレイスケールSSIM (グローバル平均)。"""
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    kernel = (11, 11)
    mu_a = cv2.GaussianBlur(a, kernel, 1.5)
    mu_b = cv2.GaussianBlur(b, kernel, 1.5)
    mu_a2, mu_b2, mu_ab = mu_a * mu_a, mu_b * mu_b, mu_a * mu_b
    sigma_a = cv2.GaussianBlur(a * a, kernel, 1.5) - mu_a2
    sigma_b = cv2.GaussianBlur(b * b, kernel, 1.5) - mu_b2
    sigma_ab = cv2.GaussianBlur(a * b, kernel, 1.5) - mu_ab
    ssim_map = ((2 * mu_ab + c1) * (2 * sigma_ab + c2)) / (
        (mu_a2 + mu_b2 + c1) * (sigma_a + sigma_b + c2)
    )
    return float(ssim_map.mean())


def _draw_line(canvas: Image.Image, line: dict, pt_per_px: float) -> None:
    size_px = int(round(line["size_pt"] / pt_per_px))
    if size_px <= 0:
        return
    try:
        font = _load_pil_font(line["font_path"], line["font_index"], size_px)
    except Exception:
        return
    ascent, _descent = font.getmetrics()
    x = line["origin_x_px"]
    y = line["baseline_y_px"] - ascent
    spacing_px = line["char_spacing_pt"] / pt_per_px

    text = line["text"]
    if line.get("gradient"):
        top = tuple(line["gradient"][0])
        bottom = tuple(line["gradient"][1])
        mask = Image.new("L", canvas.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        _draw_spaced(mask_draw, x, y, text, font, spacing_px, 255)
        gradient = _vertical_gradient(canvas.size, top, bottom)
        canvas.paste(gradient, (0, 0), mask)
    else:
        draw = ImageDraw.Draw(canvas)
        _draw_spaced(draw, x, y, text, font, spacing_px, tuple(line["color"]))


def _draw_spaced(draw, x: float, y: float, text: str, font, spacing_px: float, fill) -> None:
    if abs(spacing_px) < 0.05:
        draw.text((x, y), text, font=font, fill=fill)
        return
    cx = x
    for char in text:
        draw.text((cx, y), char, font=font, fill=fill)
        cx += font.getlength(char) + spacing_px


def _draw_overlay(canvas: Image.Image, overlay: dict, library: FontLibrary) -> None:
    from .pptx_writer import _map_pdf_font

    x0, _y0, x1, y1 = overlay["bbox_px"]
    size_px = int(round(overlay["size_pt_pdf"] * overlay.get("px_per_pt", 1.0)))
    family = _map_pdf_font(overlay["font"], library, overlay["text"])
    face = library.face(family, bold=overlay.get("bold", False))
    if face is None or size_px <= 0:
        return
    try:
        font = _load_pil_font(face.path, face.index, size_px)
    except Exception:
        return
    ascent, _descent = font.getmetrics()
    baseline = overlay.get("origin_y_px", y1)
    draw = ImageDraw.Draw(canvas)
    draw.text((x0, baseline - ascent), overlay["text"], font=font,
              fill=tuple(overlay["color"]))


def _vertical_gradient(size: tuple[int, int], top, bottom) -> Image.Image:
    w, h = size
    ramp = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    top_arr = np.array(top, np.float32)[None, None, :]
    bottom_arr = np.array(bottom, np.float32)[None, None, :]
    grad = (top_arr * (1 - ramp) + bottom_arr * ramp).astype(np.uint8)
    return Image.fromarray(np.repeat(grad, w, axis=1), "RGB")


def run_qa(processed: list[dict], pages_dir: Path, settings: Settings,
           library: FontLibrary) -> dict:
    results = {}
    for record in sorted(processed, key=lambda r: r["number"]):
        page_dir = pages_dir / f"{record['number']:03d}"
        layout = json.loads((page_dir / "layout.json").read_text(encoding="utf-8"))
        source = cv2.imread(str(page_dir / "source.png"))
        background = cv2.imread(str(page_dir / "background.png"))
        if source is None or background is None:
            continue
        pt_per_px = layout["pt_per_px"]

        canvas = Image.fromarray(cv2.cvtColor(background, cv2.COLOR_BGR2RGB))
        for block in layout["blocks"]:
            for line in block["lines"]:
                _draw_line(canvas, line, pt_per_px)
        for overlay in layout.get("overlay_texts", []):
            _draw_overlay(canvas, overlay, library)
        rendered = cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)
        cv2.imwrite(str(page_dir / "qa_render.png"), rendered)

        gray_src = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
        gray_ren = cv2.cvtColor(rendered, cv2.COLOR_BGR2GRAY)
        score = _ssim(gray_src, gray_ren)

        diff = cv2.absdiff(source, rendered)
        cv2.imwrite(str(page_dir / "qa_diff.png"), diff)

        side = np.hstack([source, rendered])
        cv2.imwrite(str(page_dir / "qa_side_by_side.jpg"), side,
                    [cv2.IMWRITE_JPEG_QUALITY, 88])

        results[str(record["number"])] = {
            "ssim": round(score, 4),
            "n_review": record.get("n_review", 0),
        }
    return results
