"""スタイルソルバー: 実測インクと候補フォントの照合レンダリングにより、
フォント・サイズ・字間・太さ・色・位置を決定論的に解く。

原理:
  候補フォントで同じ文字列を基準サイズで描画し、インク形状(高さ・幅・密度)を
  実測値と比較する。高さ比からサイズが、幅の残差から字間(spc)が一意に決まる。
  描画原点とインクの相対位置は既知なので、ベースラインのキャンバス座標も逆算できる。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import PORTABLE_FONT_FAMILIES, Settings
from .fontlib import FontFace, FontLibrary, REF_SIZE, missing_glyphs, render_metrics
from .inkstats import InkStats

JP_RE = re.compile(r"[　-ヿ㐀-鿿豈-﫿＀-￯]")


def has_japanese(text: str) -> bool:
    """和文フォント候補を使うべきか(多数決)。

    全角記号1文字(「：」等)だけで和文扱いになると、実質欧文の行が
    等幅・コンデンス系候補を失うため、和文文字の比率で判定する。
    """
    meaningful = [c for c in text if not c.isspace()]
    if not meaningful:
        return False
    jp_count = sum(1 for c in meaningful if JP_RE.match(c))
    return jp_count / len(meaningful) > 0.25


def _clamp_spacing_pt(spacing_pt: float, size_pt: float, settings: Settings) -> float:
    """字間の可読性クランプ。

    負方向(詰め)は -max_negative_spacing_em × サイズ を下限とする。
    これ以上詰めると文字が重なって読めないため、幅一致より可読性を優先する。
    """
    lower = -min(settings.max_char_spacing_pt,
                 settings.max_negative_spacing_em * size_pt)
    return max(lower, min(settings.max_char_spacing_pt, spacing_pt))


@dataclass
class SolvedStyle:
    face: FontFace
    size_pt: float
    char_spacing_pt: float
    bold: bool
    color: tuple[int, int, int]
    gradient: tuple[tuple[int, int, int], tuple[int, int, int]] | None
    origin_x_px: float        # 先頭グリフの描画原点 (キャンバスpx)
    baseline_y_px: float      # ベースライン (キャンバスpx)
    advance_w_px: float       # 行全体の送り幅 (字間込み, キャンバスpx)
    score: float              # ヒューリスティック照合誤差 (小さいほど良い)
    width_error: float        # 字間適用前の幅誤差率
    missing: str = ""         # 選択フォントに無い文字 (豆腐化する)
    ncc: float = 0.0          # テンプレート照合の相関 (大きいほど良い, 0=未実施)


@dataclass
class Candidates:
    jp: list[FontFace] = field(default_factory=list)
    latin: list[FontFace] = field(default_factory=list)


def collect_candidates(library: FontLibrary, settings: Settings,
                       jp_families: tuple[str, ...], latin_families: tuple[str, ...]) -> Candidates:
    result = Candidates()
    seen_jp: set[tuple[str, int, bool]] = set()
    seen_latin: set[tuple[str, int, bool]] = set()
    for family in jp_families:
        for face in library.find(family):
            key = (face.typeface, face.weight, face.bind_bold)
            if not face.italic and key not in seen_jp:
                seen_jp.add(key)
                result.jp.append(face)
    for family in latin_families:
        for face in library.find(family):
            key = (face.typeface, face.weight, face.bind_bold)
            if not face.italic and key not in seen_latin:
                seen_latin.add(key)
                result.latin.append(face)
    return result


def solve_line(text: str, ink: InkStats, candidates: Candidates,
               pt_per_px: float, settings: Settings) -> SolvedStyle | None:
    """1行のテキストのスタイルを解く(ヒューリスティック最良のみ)。"""
    top = solve_line_topk(text, ink, candidates, pt_per_px, settings, k=1)
    return top[0] if top else None


def solve_line_topk(text: str, ink: InkStats, candidates: Candidates,
                    pt_per_px: float, settings: Settings, k: int = 3) -> list[SolvedStyle]:
    """ヒューリスティック照合スコア順に上位k件の候補スタイルを返す。"""
    # プール選択: 和文主体→和文候補 / 純欧文→欧文候補 /
    # 和文文字を少しでも含む混在行→両方の和集合。
    # (欧文専用フォントはかな・約物を持たず、混在行に選ぶと
    #  サイズ崩壊やはみ出しを起こす。未収録グリフのペナルティが
    #  働くよう和文候補を必ず土俵に乗せる)
    # 幾何記号(◎○● 等)は和文フォントが収録し欧文フォントは持たないことが
    # 多いため、含む行は和文候補も土俵に乗せる(未収録ペナルティが働く)
    jp_any = bool(JP_RE.search(text)) or bool(set(text) & set("◎○●〇■□▲△▼▽★☆※"))
    if has_japanese(text):
        pool = candidates.jp
    elif jp_any:
        pool = candidates.jp + candidates.latin
    else:
        pool = candidates.latin
    if not pool:
        pool = candidates.jp or candidates.latin
    if not pool:
        return []

    observed_stroke_ratio = ink.stroke_width / max(ink.ink_h, 1)

    results: list[SolvedStyle] = []
    for face in pool:
        rm = render_metrics(text, face.path, face.index)
        if rm is None or rm.ink_h <= 0:
            continue
        absent = missing_glyphs(text, face.path, face.index)
        scale = ink.ink_h / rm.ink_h
        size_px = REF_SIZE * scale
        size_pt = size_px * pt_per_px

        # 幅残差 → 字間
        predicted_w = rm.ink_w * scale
        width_error = (predicted_w - ink.ink_w) / max(ink.ink_w, 1)
        spacing_px = (ink.ink_w - predicted_w) / rm.n_gaps if rm.n_gaps else 0.0
        spacing_pt = spacing_px * pt_per_px
        spacing_pt = _clamp_spacing_pt(spacing_pt, size_pt, settings)

        # 太さ照合: ストローク幅/インク高さ の比を実測と比較
        rendered_stroke_ratio = rm.stroke_width / max(rm.ink_h, 1)
        stroke_error = abs(rendered_stroke_ratio - observed_stroke_ratio) / max(
            observed_stroke_ratio, 1e-6)

        # スコア: 幅誤差(フォルム乖離。字間で吸収はするが小さいほど良い) + 太さ誤差
        # + 未収録グリフの強ペナルティ(豆腐化回避)
        # + 非ポータブルフォントのペナルティ(受け渡し先での代替崩れ防止)
        score = abs(width_error) * 1.0 + stroke_error * 0.5 + len(absent) * 0.75
        if face.family not in PORTABLE_FONT_FAMILIES:
            score += settings.non_portable_penalty

        origin_x = ink.ink_bbox[0] - rm.ink_left * scale
        baseline_y = ink.ink_bbox[1] - rm.ink_top_from_ascent * scale + rm.ascent * scale
        spacing_px_applied = spacing_pt / pt_per_px
        advance_w = rm.adv_w * scale + spacing_px_applied * rm.n_gaps

        results.append(SolvedStyle(
            face=face,
            size_pt=round(size_pt, 2),
            char_spacing_pt=round(spacing_pt, 2),
            bold=face.bind_bold,
            color=ink.color,
            gradient=_gradient_of(ink, settings),
            origin_x_px=origin_x,
            baseline_y_px=baseline_y,
            advance_w_px=advance_w,
            score=score,
            width_error=width_error,
            missing=absent,
        ))
    results.sort(key=lambda s: s.score)
    return results[:k]


def refine_line(text: str, ink: InkStats, image, candidates: Candidates,
                pt_per_px: float, settings: Settings,
                size_scale_hint: float = 1.0,
                det_height_px: float | None = None) -> SolvedStyle | None:
    """行の最終解決: ヒューリスティック上位候補をテンプレート照合(NCC)で
    再評価し、最も画像に合うフォント・サイズ・位置を採用する。

    選択キーはNCCを主とし、以下の軽い正則化を加える:
    - サイズ乖離: グローが字画を太らせ「大きいサイズ+強い詰め」が
      相関上勝ってしまう吸引を抑える
    - 字間の大きさ: 過大なトラッキングは編集時の破綻リスク
    - 非ポータブルフォント: 受け渡し先での代替リスク (タイブレーク程度)
    """
    ranked = solve_line_topk(text, ink, candidates, pt_per_px, settings, k=14)
    if not ranked:
        return None
    # ショートリスト: スコア上位3 + 未登場のウェイト帯(細/中/太)の最良を追加。
    # ストローク計測はグロー・JPEG劣化で不確かなため、ウェイトの最終判定は
    # NCC(実描画との相関)に委ねる。そのために各帯の候補を必ず1つは残す。
    def bucket(weight: int) -> int:
        if weight < 450:
            return 0
        if weight <= 650:
            return 1
        return 2

    shortlist: list[SolvedStyle] = []
    seen_buckets: set[int] = set()
    for i, style in enumerate(ranked):
        b = bucket(style.face.weight)
        if i < 6 or b not in seen_buckets:
            shortlist.append(style)
            seen_buckets.add(b)
    best_style = shortlist[0]
    best_key = -1e9
    for style in shortlist:
        seeded = style
        if size_scale_hint != 1.0:
            seeded = SolvedStyle(**{**style.__dict__,
                                    "size_pt": style.size_pt * size_scale_hint})
        refined = refine_by_template(image, text, style.face, seeded, ink,
                                     pt_per_px, settings)
        if refined is None:
            continue
        refined_style, ncc = refined
        size_px = refined_style.size_pt / pt_per_px
        seed_px = seeded.size_pt / pt_per_px
        dev = size_px / max(seed_px, 1.0) - 1.0
        # 上方向の乖離(グローの太らせに引かれる)は強く、下方向は弱く抑制
        size_dev_penalty = 0.5 * dev if dev > 0 else 0.15 * (-dev)
        spc_em = abs(refined_style.char_spacing_pt / max(refined_style.size_pt, 1.0))
        key = ncc - size_dev_penalty - 0.25 * spc_em
        # 小さい文字の過剰boldの抑制: JPEG圧縮された小文字はストロークが
        # 太めに測れるため、僅差なら軽いウェイトを選ぶ(真に太い文字は
        # 相関差が大きく出るので影響しない)
        if ink.ink_h < 20 and style.face.weight > 500:
            key -= 0.02 * (style.face.weight - 500) / 400
        # OCR検出ボックス高によるサイズ上限プライア:
        # emサイズが検出高の95%を超えるのは物理的に不自然(過大解の抑制)。
        # グローで検出ボックス自体が膨らんでいる場合は比が小さくなり無効化される。
        if det_height_px:
            over = size_px / det_height_px - 0.95
            if over > 0:
                key -= 1.5 * over
        if style.face.family not in PORTABLE_FONT_FAMILIES:
            key -= 0.02
        if style.missing:
            key -= 0.50
        if key > best_key:
            best_key = key
            best_style = refined_style
    if best_key == -1e9 and det_height_px:
        # 全候補で照合不成立(まれ)。素の推定は隣接行のインク混入等で
        # 過大なことがあるため、OCR検出枠の高さでサイズを上限クランプする
        max_size_pt = det_height_px * 0.88 * pt_per_px
        if best_style.size_pt > max_size_pt:
            clamped = resolve_with(text, ink, best_style.face, max_size_pt,
                                   pt_per_px, settings)
            if clamped is not None:
                best_style = clamped
    elif det_height_px and (best_style.ncc or 0) < 0.25:
        # 照合はしたが相関が極端に低い: テンプレート由来のサイズ・位置は
        # 信用できない(グロー・パネル発光の混入で膨張した解の典型)。
        # 検出枠の高さでサイズをクランプし、位置は再解決に委ねる。
        max_size_pt = det_height_px * 0.88 * pt_per_px
        if best_style.size_pt > max_size_pt:
            clamped = resolve_with(text, ink, best_style.face, max_size_pt,
                                   pt_per_px, settings)
            if clamped is not None:
                clamped.ncc = best_style.ncc
                best_style = clamped
    return best_style


def resolve_with(text: str, ink: InkStats, face: FontFace, size_pt: float,
                 pt_per_px: float, settings: Settings) -> SolvedStyle | None:
    """フェイスとサイズを固定して、字間と位置だけを解き直す(ブロック統一用)。"""
    rm = render_metrics(text, face.path, face.index)
    if rm is None or rm.ink_h <= 0:
        return None
    scale = (size_pt / pt_per_px) / REF_SIZE
    predicted_w = rm.ink_w * scale
    width_error = (predicted_w - ink.ink_w) / max(ink.ink_w, 1)
    spacing_px = (ink.ink_w - predicted_w) / rm.n_gaps if rm.n_gaps else 0.0
    spacing_pt = _clamp_spacing_pt(spacing_px * pt_per_px, size_pt, settings)
    spacing_px_applied = spacing_pt / pt_per_px
    # 縦位置: インクの中心で合わせる(サイズ固定時、グロー等で上下端が
    # 対称に膨らんだ計測でも中心は安定するため)
    origin_x = ink.ink_bbox[0] - rm.ink_left * scale
    ink_center = (ink.ink_bbox[1] + ink.ink_bbox[3]) / 2
    render_center_from_ascent = rm.ink_top_from_ascent + rm.ink_h / 2
    baseline_y = ink_center + (rm.ascent - render_center_from_ascent) * scale
    return SolvedStyle(
        face=face,
        size_pt=round(size_pt, 2),
        char_spacing_pt=round(spacing_pt, 2),
        bold=face.bind_bold,
        color=ink.color,
        gradient=_gradient_of(ink, settings),
        origin_x_px=origin_x,
        baseline_y_px=baseline_y,
        advance_w_px=rm.adv_w * scale + spacing_px_applied * rm.n_gaps,
        score=abs(width_error),
        width_error=width_error,
    )


def refine_by_template(image, text: str, face: FontFace, style: SolvedStyle,
                       ink: InkStats, pt_per_px: float, settings: Settings,
                       size_lock_px: int | None = None) -> tuple[SolvedStyle, float] | None:
    """テンプレート照合による微調整。

    認識テキストを候補フォントで実描画し、元画像と正規化相互相関(NCC)を取り、
    サイズ(±10%を1px刻み)と位置(±0.35em)を画素単位で直接最適化する。
    グロー・JPEG劣化・二値化しきい値の影響を受けにくく、
    「そのフォントがどれだけ画像に合うか」の最終指標(NCCスコア)も得られる。
    """
    import cv2
    import numpy as np
    from .fontlib import _load_pil_font
    from PIL import Image, ImageDraw

    size_px0 = style.size_pt / pt_per_px
    if size_px0 < 7:
        return None
    rm0 = render_metrics(text, face.path, face.index)
    if rm0 is None:
        return None

    # サイズ探索範囲: 上限は初期推定×1.12。下限は初期推定×0.70だが、
    # 「観測インク幅を送り幅で割った幅適合サイズ」まで必ず届かせる。
    # 隣接行のインク混入等で高さ由来の初期推定が膨張しても、
    # 幅情報から真のサイズへ降りられるようにするため。
    if size_lock_px is not None:
        lo_probe, hi_probe = size_lock_px, size_lock_px
    else:
        hi_probe = int(round(size_px0 * 1.12))
        adv_per_em = rm0.adv_w / REF_SIZE
        width_fit = ink.ink_w / max(adv_per_em * (1.0 - settings.max_negative_spacing_em), 1e-6)
        lo_probe = max(6, min(int(round(size_px0 * 0.70)), int(width_fit * 0.92)))
        hi_probe = max(hi_probe, lo_probe)

    # 探索領域: 期待インク位置の周囲。テンプレート(最大サイズ時)が
    # 縦横とも必ず収まるよう、領域はテンプレート想定寸法からも下限を取る。
    ih, iw = image.shape[:2]
    margin = max(6, int(size_px0 * 0.35))
    est_h = int(hi_probe * 1.8) + 4          # ascent+descent余裕込みの想定高
    est_w = int(rm0.adv_w / REF_SIZE * hi_probe) + hi_probe + 8
    cy = (ink.ink_bbox[1] + ink.ink_bbox[3]) / 2
    cx = (ink.ink_bbox[0] + ink.ink_bbox[2]) / 2
    half_h = max((ink.ink_bbox[3] - ink.ink_bbox[1]) / 2 + margin, est_h / 2 + margin)
    half_w = max((ink.ink_bbox[2] - ink.ink_bbox[0]) / 2 + margin, est_w / 2 + margin)
    ex0 = max(0, int(cx - half_w))
    ey0 = max(0, int(cy - half_h))
    ex1 = min(iw, int(cx + half_w) + 1)
    ey1 = min(ih, int(cy + half_h) + 1)
    gray = cv2.cvtColor(image[ey0:ey1, ex0:ex1], cv2.COLOR_BGR2GRAY).astype(np.float32)
    # 極性正規化: 文字が明るくなる向きへ
    bg_gray = float(np.median(gray))
    ink_gray = 0.299 * ink.color[0] + 0.587 * ink.color[1] + 0.114 * ink.color[2]
    if ink_gray < bg_gray:
        gray = 255.0 - gray

    obs_w = max(ink.ink_w, 4)
    best = None  # (ncc, size_px, spacing_px, origin_x, baseline_y)
    lo, hi = lo_probe, hi_probe + 1
    for size_px in range(lo, hi):
        try:
            font = _load_pil_font(face.path, face.index, size_px)
        except Exception:
            continue
        ascent, _descent = font.getmetrics()
        scale = size_px / REF_SIZE
        natural_w = rm0.adv_w * scale
        spacing_px = (obs_w - rm0.ink_w * scale) / rm0.n_gaps if rm0.n_gaps else 0.0
        spacing_px = max(-settings.max_negative_spacing_em * size_px,
                         max(-settings.max_char_spacing_pt / pt_per_px,
                             min(settings.max_char_spacing_pt / pt_per_px, spacing_px)))
        # 幅由来の字間が大きい正値になるのは、バー端・グロー等の混入で
        # 観測インク幅が膨張しているサインでもある。その場合は
        # 「膨張幅に合わせた字間」と「自然字間」の両方をテンプレート化し、
        # どちらが画像に合うかをNCC自身に決めさせる(真のトラッキングなら
        # 広い方が勝ち、混入なら自然字間が勝つ)。
        spacing_variants = [spacing_px]
        cap_px = settings.max_positive_spacing_em * size_px
        if spacing_px > cap_px:
            spacing_variants.append(0.0)
        for spacing_px in spacing_variants:
            template_w = int(natural_w + abs(spacing_px) * rm0.n_gaps) + size_px
            template_h = ascent + int(size_px * 0.6)
            if template_w < 4 or template_h < 4:
                continue
            canvas = Image.new("L", (template_w, template_h), 0)
            draw = ImageDraw.Draw(canvas)
            x = float(size_px) * 0.25
            origin_offset_x = x
            for char in text:
                draw.text((x, 0), char, font=font, fill=255)
                x += font.getlength(char) + spacing_px
            template = np.asarray(canvas, dtype=np.float32)
            tys, txs = np.nonzero(template > 64)
            if len(txs) < 8:
                continue
            # テンプレートをインク周辺でトリム(相関の希釈防止)
            pad = 2
            ty0, ty1 = max(0, tys.min() - pad), min(template.shape[0], tys.max() + pad + 1)
            tx0, tx1 = max(0, txs.min() - pad), min(template.shape[1], txs.max() + pad + 1)
            template = template[ty0:ty1, tx0:tx1]
            if template.shape[0] >= gray.shape[0] or template.shape[1] >= gray.shape[1]:
                continue
            result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
            # 許容変位の制限: 探索領域はテンプレートが収まるよう広いが、
            # マッチ位置は期待インク位置の近傍に限る(縦に隣接する別の行へ
            # マッチが「ジャンプ」する事故の防止)。
            # 基準は上端ではなく「中心」: グローはインクを上下対称に膨らませる
            # ため、上端は持ち上がっても中心はほぼ動かない。
            tol_y = max(3, int(size_px * 0.18))
            tol_x = max(4, int(size_px * 0.28))
            ink_cy = (ink.ink_bbox[1] + ink.ink_bbox[3]) / 2 - ey0
            ink_cx = (ink.ink_bbox[0] + ink.ink_bbox[2]) / 2 - ex0
            exp_y = int(round(ink_cy - template.shape[0] / 2))
            exp_x = int(round(ink_cx - template.shape[1] / 2))
            y_lo = max(0, exp_y - tol_y)
            y_hi = min(result.shape[0], exp_y + tol_y + 1)
            x_lo = max(0, exp_x - tol_x)
            x_hi = min(result.shape[1], exp_x + tol_x + 1)
            if y_hi <= y_lo or x_hi <= x_lo:
                continue
            window = result[y_lo:y_hi, x_lo:x_hi]
            _minv, maxv, _minl, local = cv2.minMaxLoc(window)
            maxl = (local[0] + x_lo, local[1] + y_lo)
            if best is None or maxv > best[0]:
                # 描画原点(先頭グリフのペン位置)とベースラインのキャンバス座標を逆算
                origin_x = float(ex0 + maxl[0] - tx0 + origin_offset_x)
                baseline_y = float(ey0 + maxl[1] - ty0 + ascent)
                best = (float(maxv), size_px, spacing_px, origin_x, baseline_y)

    if best is None:
        return None
    ncc, size_px, spacing_px, origin_x, baseline_y = best
    scale = size_px / REF_SIZE
    refined = SolvedStyle(
        face=face,
        size_pt=round(size_px * pt_per_px, 2),
        char_spacing_pt=round(spacing_px * pt_per_px, 2),
        bold=face.bind_bold,
        color=style.color,
        gradient=style.gradient,
        origin_x_px=origin_x,
        baseline_y_px=baseline_y,
        advance_w_px=rm0.adv_w * scale + spacing_px * rm0.n_gaps,
        score=style.score,
        width_error=style.width_error,
        missing=style.missing,
        ncc=round(ncc, 4),
    )
    return refined, ncc


def arbitrate_edge_confusables(image, text: str, style: SolvedStyle,
                               ink: InkStats, pt_per_px: float,
                               settings: Settings) -> tuple[str, SolvedStyle]:
    """行頭・行末の紛らわしい字形をテンプレート照合(NCC)で裁定する。

    タイトル装飾の波ダッシュ「〜」はOCRがカタカナ「ヘ」等として返しがちで、
    文字列としては妥当なため信頼度でも検出できない。装飾記号が現れる
    行頭・行末に限り、混同表の代替字でテンプレートを描き直して
    同一サイズ・同一フォントでNCCを比較し、画像に合う方を採用する。
    (決定論的な照合同士の比較なので、わずかな差でも意味を持つ)
    """
    table = {"ヘ": "〜", "へ": "〜", "~": "〜"}
    if not text or style is None:
        return text, style
    variants = []
    if text[0] in table:
        variants.append(table[text[0]] + text[1:])
    if len(text) > 1 and text[-1] in table:
        variants.append(text[:-1] + table[text[-1]])
    if not variants:
        return text, style
    size_px = max(7, int(round(style.size_pt / pt_per_px)))
    base = refine_by_template(image, text, style.face, style, ink, pt_per_px,
                              settings, size_lock_px=size_px)
    best_text, best_style = text, style
    best_ncc = base[1] if base else (style.ncc or 0.0)
    # 差は1文字分の局所相関のみで、決定論的な照合同士の比較なので
    # マージンは「同点回避」程度の小ささでよい
    import os
    debug = bool(os.environ.get("NBLM_DEBUG_CONFUSABLE"))
    if debug:
        print(f"[confusable] base ncc={best_ncc:.4f} text={text[:20]!r}")
    for variant in variants:
        ref = refine_by_template(image, variant, style.face, style, ink,
                                 pt_per_px, settings, size_lock_px=size_px)
        if debug and ref is not None:
            print(f"[confusable] variant ncc={ref[1]:.4f} text={variant[:20]!r}")
        if ref is not None and ref[1] > best_ncc + 0.001:
            best_text, best_style, best_ncc = variant, ref[0], ref[1]
    return best_text, best_style


def _gradient_of(ink: InkStats, settings: Settings):
    top = ink.color_top
    bottom = ink.color_bottom
    delta = sum((a - b) ** 2 for a, b in zip(top, bottom)) ** 0.5
    if delta >= settings.gradient_min_delta:
        return (top, bottom)
    return None
