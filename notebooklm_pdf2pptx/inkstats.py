"""テキスト行のインク計測: マスク、色、グラデーション、ストローク幅。

すべて局所計測(行bboxの少し外側をリングとして背景推定)で、
デザイン全体には依存しない。
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class InkStats:
    # --- コア(字画そのもの): サイズ・位置・色の計測に使う。グローを含まない ---
    ink_bbox: tuple[int, int, int, int]          # キャンバスpx (x0,y0,x1,y1) inclusive
    color: tuple[int, int, int]                   # インクの代表色 (RGB)
    color_top: tuple[int, int, int]               # 上1/3の代表色
    color_bottom: tuple[int, int, int]            # 下1/3の代表色
    stroke_width: float                           # 距離変換によるストローク幅(px)
    ink_h: int
    ink_w: int
    ink_pixels: int
    # --- フル(グロー・ハロー込み): テキスト除去マスクに使う ---
    ink_mask_local: np.ndarray                    # 拡張領域内のフルマスク (uint8 0/255)
    region: tuple[int, int, int, int]             # ink_mask_localのキャンバス上の位置
    full_bbox: tuple[int, int, int, int]          # フルマスクのbbox (キャンバスpx)
    has_glow_core: bool                           # 二段階分離でコアを抽出したか
    # --- 周辺 ---
    bg_std: float                                 # 背景リングの色標準偏差(フラット判定用)
    bg_color: tuple[int, int, int]
    # --- 計測メタ ---
    text_brighter: bool = True                    # 採用した極性(文字が背景より明るいか)
    core_mask_local: np.ndarray | None = None     # コアマスク(拡張領域内、色ラン計測用)


def measure_ink(image: np.ndarray, bbox: tuple[float, float, float, float],
                pad_ratio: float = 0.18,
                force_polarity: bool | None = None) -> InkStats | None:
    h, w = image.shape[:2]
    x0, y0, x1, y1 = bbox
    bh = max(y1 - y0, 4.0)
    pad_x = int(bh * pad_ratio) + 2
    pad_y = int(bh * pad_ratio) + 2
    ex0 = max(0, int(x0) - pad_x)
    ey0 = max(0, int(y0) - pad_y)
    ex1 = min(w, int(round(x1)) + pad_x)
    ey1 = min(h, int(round(y1)) + pad_y)
    if ex1 - ex0 < 3 or ey1 - ey0 < 3:
        return None
    crop = image[ey0:ey1, ex0:ex1]
    rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32)

    # 背景推定: 元のbboxの外側リング
    ring = np.ones(crop.shape[:2], bool)
    iy0, iy1 = int(y0) - ey0, int(round(y1)) - ey0
    ix0, ix1 = int(x0) - ex0, int(round(x1)) - ex0
    ring[max(0, iy0):max(0, iy1), max(0, ix0):max(0, ix1)] = False
    ring_px = rgb[ring]
    if len(ring_px) < 16:
        ring_px = rgb.reshape(-1, 3)
    bg = np.median(ring_px, axis=0)
    bg_std = float(np.mean(np.std(ring_px, axis=0)))

    # インク抽出: 局所Otsu(グレイスケール)。
    # 極性(文字が明るいか暗いか)は「bbox内部の少数派側」で決める。
    # 文字は通常bbox面積の半分未満なので、これは「暗いページ上の明るい帯の
    # 上の暗い文字」のような入れ子背景でも正しく働く。
    # 曖昧(45〜55%)な場合のみ、外側リングの背景色から遠い側を採る。
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _thr, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    interior = ~ring
    interior_white = float(np.count_nonzero(binary[interior])) / max(
        int(np.count_nonzero(interior)), 1)
    bg_gray = float(cv2.cvtColor(
        np.uint8([[list(reversed(bg.astype(int).tolist()))]]), cv2.COLOR_BGR2GRAY)[0, 0])
    if force_polarity is not None:
        # 極性の明示指定: 明るい帯の上の白文字等、少数派則が外れるケースの
        # リトライ用(呼び出し側がNCC比較で採否を決める)
        text_brighter = force_polarity
    elif interior_white < 0.45:
        text_brighter = True
    elif interior_white > 0.55:
        text_brighter = False
    else:
        text_brighter = abs(255 - bg_gray) > abs(0 - bg_gray)
    ink = binary if text_brighter else cv2.bitwise_not(binary)

    # bbox外周由来の誤検出を除去: リング部分はインクにしない
    ink[ring] = 0

    # 帯(バー)のエッジや罫線の混入除去: 領域幅の大半に渡る扁平な連結成分は
    # 文字ではない(色付き帯の上下縁の影が典型)。
    region_w = ink.shape[1]
    region_h = ink.shape[0]
    n_labels, labels, stats, _cent = cv2.connectedComponentsWithStats(ink, connectivity=8)
    for label in range(1, n_labels):
        _x, _y, comp_w, comp_h, _area = stats[label]
        if comp_w > region_w * 0.85 and comp_h < region_h * 0.30:
            ink[labels == label] = 0

    ys, xs = np.nonzero(ink)
    if len(xs) < 8:
        return None
    full_x0, full_x1 = int(xs.min()), int(xs.max())
    full_y0, full_y1 = int(ys.min()), int(ys.max())

    # コア抽出: 「背景輝度とインクピーク輝度の中点」でしきい値を切る。
    # ガウスぼけ・グロー・JPEG劣化があってもエッジは半値点にとどまるため、
    # Otsu(分散基準で光彩に引きずられる)より字画境界の推定が安定する。
    core = ink
    has_core = False
    ink_values = gray[ink > 0].astype(np.float64)
    if len(ink_values) >= 32:
        peak = float(np.percentile(ink_values, 92 if text_brighter else 8))
        midpoint = (bg_gray + peak) / 2.0
        candidate = ink.copy()
        if text_brighter:
            candidate[gray < midpoint] = 0
        else:
            candidate[gray > midpoint] = 0
        n_candidate = int(np.count_nonzero(candidate))
        if n_candidate >= max(24, int(np.count_nonzero(ink) * 0.10)):
            cys, cxs = np.nonzero(candidate)
            core_h = cys.max() - cys.min() + 1
            full_h = full_y1 - full_y0 + 1
            core = candidate
            has_core = bool(core_h <= full_h * 0.93)

    cys, cxs = np.nonzero(core)
    ink_x0, ink_x1 = int(cxs.min()), int(cxs.max())
    # 高さは行密度トリム (fontlib.render_metricsと同一規則) で決める。
    # グローの裾や孤立ノイズ行に影響されない安定した「主要インク高」。
    from .fontlib import _density_rows

    ink_y0, ink_y1 = _density_rows(core)

    dist = cv2.distanceTransform(core, cv2.DIST_L2, 5)
    positive = dist[dist > 0]
    stroke = float(np.median(positive) * 2.0) if len(positive) else 1.0

    def pure_color(pixels: np.ndarray) -> np.ndarray:
        """縁のアンチエイリアス・JPEG混色を除いた「純色」を推定する。

        背景色から最も遠い上位30%の画素のチャネル別メディアン。
        メディアン単独だと縁の混色で白が灰色にくすむ(可読性低下の主因)。
        """
        if len(pixels) == 0:
            return np.array(bg)
        if len(pixels) < 12:
            return np.median(pixels, axis=0)
        distance = np.linalg.norm(pixels - np.asarray(bg, np.float32), axis=1)
        cutoff = np.percentile(distance, 70)
        selected = pixels[distance >= cutoff]
        if len(selected) < 4:
            selected = pixels
        return np.median(selected, axis=0)

    ink_px = rgb[core > 0]
    color = pure_color(ink_px)

    span = ink_y1 - ink_y0 + 1
    third = max(1, span // 3)
    top_sel = core.copy()
    top_sel[ink_y0 + third:, :] = 0
    bot_sel = core.copy()
    bot_sel[: ink_y1 - third + 1, :] = 0
    color_top = pure_color(rgb[top_sel > 0])
    color_bottom = pure_color(rgb[bot_sel > 0])

    def to_rgb(v) -> tuple[int, int, int]:
        return tuple(int(np.clip(round(c), 0, 255)) for c in v)

    return InkStats(
        ink_bbox=(ex0 + ink_x0, ey0 + ink_y0, ex0 + ink_x1, ey0 + ink_y1),
        color=to_rgb(color),
        color_top=to_rgb(color_top),
        color_bottom=to_rgb(color_bottom),
        stroke_width=stroke,
        ink_h=span,
        ink_w=ink_x1 - ink_x0 + 1,
        ink_pixels=int(np.count_nonzero(core)),
        ink_mask_local=ink,
        region=(ex0, ey0, ex1, ey1),
        full_bbox=(ex0 + full_x0, ey0 + full_y0, ex0 + full_x1, ey0 + full_y1),
        has_glow_core=has_core,
        bg_std=bg_std,
        bg_color=to_rgb(bg),
        text_brighter=text_brighter,
        core_mask_local=core,
    )
