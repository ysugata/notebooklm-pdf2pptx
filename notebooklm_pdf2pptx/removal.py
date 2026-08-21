"""テキスト除去: 適応式。

- フラット/緩勾配背景: タイトなインクマスク + 古典インペイント(決定論・高速・高品質)
- 複雑背景(写真・グロー): LaMa (IOPaint, ローカル推論) 用のマスクに回す。
  LaMa無効時は古典マルチスケールで代替(品質は劣るが動作は完結)。

判定は行ごとに局所計測(背景リングの分散 + ハロー検出)で行う。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import cv2
import numpy as np

from .config import Settings
from .inkstats import InkStats


def _halo_stats(image: np.ndarray, ink: InkStats) -> tuple[float, tuple[int, int, int]]:
    """インク近傍(マスク外側の帯)の背景色からの逸脱率と、その帯の代表色。

    逸脱率が高ければグロー(光彩)付き文字。代表色はグロー色の推定に使う。
    """
    x0, y0, x1, y1 = ink.region
    crop = image[y0:y1, x0:x1]
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    dilated = cv2.dilate(ink.ink_mask_local, kernel)
    band = cv2.subtract(dilated, ink.ink_mask_local)
    band_px = rgb[band > 0]
    if len(band_px) < 12:
        return 0.0, ink.bg_color
    bg = np.array(ink.bg_color, np.float32)
    distances = np.linalg.norm(band_px - bg, axis=1)
    deviant = band_px[distances > 24]
    fraction = float(len(deviant) / len(band_px))
    color = tuple(int(np.clip(round(v), 0, 255))
                  for v in (np.median(deviant, axis=0) if len(deviant) else bg))
    return fraction, color


def _halo_fraction(image: np.ndarray, ink: InkStats) -> float:
    return _halo_stats(image, ink)[0]


def detect_glow(image: np.ndarray, ink: InkStats, pt_per_px: float) -> dict | None:
    """グロー(発光ハロー)付き文字なら {radius_pt, color} を返す。

    アンチエイリアス・JPEG劣化の遷移帯を光彩と誤認しないため、条件は厳格に:
    - ハロー帯の逸脱率が高い (>=0.50)
    - ハローが「発光」している: 背景より明確に明るい (輝度 +25以上)
    - 小さすぎる文字 (コア高 <16px) は対象外 (視認できず、暗色誤付与の温床)
    """
    if ink.ink_h < 16:
        return None
    spread = (ink.full_bbox[3] - ink.full_bbox[1] + 1) - ink.ink_h
    fraction, color = _halo_stats(image, ink)
    if fraction < 0.50:
        return None
    halo_lum = 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]
    bg = ink.bg_color
    bg_lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    if halo_lum < bg_lum + 25:
        return None
    # グロー色はコアとフルインクの間の「発光帯」から採る方が正確
    # (フルインク外側の帯は減衰した外縁で、濁った暗色になりがち)。
    x0, y0, x1, y1 = ink.region
    crop = image[y0:y1, x0:x1]
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32)
    glow_band = None
    try:
        core_bbox_local = (ink.ink_bbox[0] - x0, ink.ink_bbox[1] - y0,
                           ink.ink_bbox[2] - x0, ink.ink_bbox[3] - y0)
        band_mask = ink.ink_mask_local.copy()
        band_mask[core_bbox_local[1]:core_bbox_local[3] + 1,
                  core_bbox_local[0]:core_bbox_local[2] + 1] = 0
        band_px = rgb[band_mask > 0]
        if len(band_px) >= 200:
            glow_band = tuple(int(np.clip(round(v), 0, 255))
                              for v in np.median(band_px, axis=0))
    except Exception:
        glow_band = None
    if glow_band is not None:
        band_lum = 0.299 * glow_band[0] + 0.587 * glow_band[1] + 0.114 * glow_band[2]
        if band_lum >= bg_lum + 25:
            color = glow_band
    radius_px = max(spread / 2.0, ink.stroke_width * 1.8)
    radius_pt = max(1.0, min(8.0, radius_px * pt_per_px))
    return {"radius_pt": round(radius_pt, 2), "color": list(color)}


def classify_region(image: np.ndarray, ink: InkStats, settings: Settings) -> str:
    """flat | complex"""
    if ink.bg_std <= settings.flat_std_threshold and _halo_fraction(image, ink) < 0.22:
        return "flat"
    return "complex"


def complex_mask(image: np.ndarray, ink: InkStats, settings: Settings) -> np.ndarray:
    """複雑背景用マスクの適応選択。

    - グロー(ハロー)を伴う文字: 矩形マスク(光彩ごと消す必要がある)
    - テクスチャ背景(地図・写真等)上の文字: インク膨張のみ
      (矩形だと周囲のデザイン要素まで消えてしまう。LaMaは有機的な
       マスク周辺のテクスチャ再構成が得意なのでこれで十分)
    """
    halo = _halo_fraction(image, ink)
    if halo >= 0.25 and ink.bg_std < 26.0:
        return rect_mask(ink, image.shape[:2])
    return region_mask(ink, image.shape[:2], grow=5)


def region_mask(ink: InkStats, shape: tuple[int, int], grow: int = 3) -> np.ndarray:
    """キャンバス全体サイズのマスクへ、このインク領域を展開する。"""
    mask = np.zeros(shape, np.uint8)
    x0, y0, x1, y1 = ink.region
    kernel_size = max(3, int(round(ink.stroke_width)) + grow)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    grown = cv2.dilate(ink.ink_mask_local, kernel)
    mask[y0:y1, x0:x1] = grown
    return mask


def rect_mask(ink: InkStats, shape: tuple[int, int], pad_ratio: float = 0.28) -> np.ndarray:
    """複雑背景用: フルインク(グロー込み)bboxを膨張させた矩形マスク。"""
    mask = np.zeros(shape, np.uint8)
    x0, y0, x1, y1 = ink.full_bbox
    h = y1 - y0 + 1
    pad = int(h * pad_ratio) + 3
    mask[max(0, y0 - pad):min(shape[0], y1 + pad + 1),
         max(0, x0 - pad):min(shape[1], x1 + pad + 1)] = 255
    return mask


def ghost_mask(src: np.ndarray, out: np.ndarray,
               mask: np.ndarray) -> np.ndarray | None:
    """修復結果に残るテキストの「残像(ゴースト)」領域を実測で検出する。

    強コントラスト文字やグレア(強い光)上の文字は1回のインペイントで
    消え切らず、ぼやけた文字模様が背景に残ることがある。マスクの連結成分
    ごとに「修復後も元のインク模様と相関が残り、かつ周囲より模様の起伏が
    大きい」場合をゴーストと判定し、膨張させた再修復マスクを返す。
    ゴーストが無ければ None。判定は全て相対量で、デザインに依存しない。
    """
    a = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY).astype(np.float32)
    b = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY).astype(np.float32)
    n_labels, labels, stats, _cent = cv2.connectedComponentsWithStats(
        (mask > 0).astype(np.uint8), connectivity=8)
    ghost = np.zeros_like(mask)
    found = False
    for label in range(1, n_labels):
        x, y, w, h, area = stats[label]
        if area < 80:
            continue
        comp = labels == label
        ring = cv2.dilate(comp.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
        ring &= ~comp
        av, bv = a[comp], b[comp]
        if len(av) < 60 or len(b[ring]) < 30:
            continue
        av_c, bv_c = av - av.mean(), bv - bv.mean()
        denom = float(np.sqrt((av_c ** 2).sum() * (bv_c ** 2).sum()))
        corr = float((av_c * bv_c).sum() / denom) if denom > 1e-6 else 0.0
        inside_std = float(bv.std())
        ring_std = float(b[ring].std())
        if corr > 0.50 and inside_std > max(10.0, ring_std * 1.5):
            grow = max(4, int(h * 0.25))
            kernel = np.ones((grow, grow), np.uint8)
            ghost = np.maximum(
                ghost, cv2.dilate(comp.astype(np.uint8) * 255, kernel))
            found = True
    return ghost if found else None


def remove_flat(image: np.ndarray, masks: list[np.ndarray]) -> np.ndarray:
    if not masks:
        return image
    combined = np.zeros(image.shape[:2], np.uint8)
    for mask in masks:
        combined = np.maximum(combined, mask)
    return cv2.inpaint(image, combined, 5, cv2.INPAINT_TELEA)


def classic_complex_fallback(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """LaMaなしの代替: 1/4スケールで大域インペイント + シーム平滑。"""
    h, w = image.shape[:2]
    small = cv2.resize(image, (max(w // 4, 8), max(h // 4, 8)), interpolation=cv2.INTER_AREA)
    mask_small = cv2.resize(mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
    mask_small = cv2.dilate(mask_small, np.ones((3, 3), np.uint8))
    filled_small = cv2.inpaint(small, mask_small, 7, cv2.INPAINT_TELEA)
    filled = cv2.resize(filled_small, (w, h), interpolation=cv2.INTER_CUBIC)
    soft = cv2.GaussianBlur(mask, (0, 0), 2).astype(np.float32) / 255.0
    soft = soft[..., None]
    return (filled.astype(np.float32) * soft + image.astype(np.float32) * (1 - soft)).astype(np.uint8)


def _iopaint_executable(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "iopaint.exe"
    return venv / "bin" / "iopaint"


def lama_available(settings: Settings) -> bool:
    return _iopaint_executable(settings.lama_venv).is_file()


def _select_device(venv: Path) -> str:
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    probe = (
        "import platform, torch; print('cuda' if torch.cuda.is_available() else "
        "('mps' if platform.system() == 'Darwin' and platform.machine() == 'arm64' "
        "and hasattr(torch.backends, 'mps') and torch.backends.mps.is_available() else 'cpu'))"
    )
    try:
        result = subprocess.run([str(python), "-c", probe], check=True,
                                capture_output=True, text=True, timeout=120)
        return result.stdout.strip() or "cpu"
    except Exception:
        return "cpu"


def run_lama_batch(image_dir: Path, mask_dir: Path, output_dir: Path, settings: Settings) -> None:
    """複数ページ分をまとめて1プロセスで処理する(モデルロードを1回に)。"""
    iopaint = _iopaint_executable(settings.lama_venv)
    if not iopaint.is_file():
        raise RuntimeError("IOPaint がありません。`python bootstrap.py --with-lama` を実行してください。")
    device = _select_device(settings.lama_venv)
    output_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(iopaint), "run", "--model=lama", f"--device={device}",
         "--image", str(image_dir), "--mask", str(mask_dir), "--output", str(output_dir)],
        check=True,
    )
