"""OCRエンジン: RapidOCR (onnxruntime) + PP-OCRv6 japan。

行単位の検出・認識。単語ボックス(word_results)から語間スペースを復元する。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import Settings

logging.getLogger("RapidOCR").setLevel(logging.WARNING)


@dataclass
class OcrLine:
    text: str
    confidence: float
    bbox: tuple[float, float, float, float]     # キャンバスpx (x0,y0,x1,y1)
    quad: list[tuple[float, float]]              # キャンバスpx 4点
    word_boxes: list[tuple[str, tuple[float, float, float, float]]] = field(default_factory=list)


class OcrEngine:
    def __init__(self, settings: Settings) -> None:
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        self.settings = settings
        # PP-OCRv6は50言語統一モデル(japan指定でも同一モデル)。
        # small=5.2M(日本語88.2%) / medium=19M(90.5%)。tinyは日本語非対応。
        model_type = ModelType.MEDIUM if settings.ocr_model_type == "medium" else ModelType.SMALL
        self._engine = RapidOCR(
            params={
                "Rec.lang_type": LangRec.JAPAN,
                "Rec.ocr_version": OCRVersion.PPOCRV6,
                "Rec.model_type": model_type,
                "Global.log_level": "warning",
            }
        )

    def recognize(self, image: np.ndarray) -> list[OcrLine]:
        scale = self.settings.ocr_upscale
        if scale != 1.0:
            work = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
        else:
            work = image
        result = self._engine(work, return_word_box=True)
        lines: list[OcrLine] = []
        if result.boxes is None:
            return lines
        word_results = list(result.word_results) if result.word_results is not None else []
        for i, (box, text, score) in enumerate(zip(result.boxes, result.txts, result.scores)):
            text = str(text).strip()
            if not text:
                continue
            if float(score) < self.settings.min_confidence:
                continue
            quad = [(float(p[0]) / scale, float(p[1]) / scale) for p in box]
            xs = [p[0] for p in quad]
            ys = [p[1] for p in quad]
            words: list[tuple[str, tuple[float, float, float, float]]] = []
            if i < len(word_results):
                try:
                    for word, _wscore, wbox in _iter_words(word_results[i]):
                        if wbox is None:
                            continue
                        wxs = [float(p[0]) / scale for p in wbox]
                        wys = [float(p[1]) / scale for p in wbox]
                        words.append((word, (min(wxs), min(wys), max(wxs), max(wys))))
                except Exception:
                    words = []
            lines.append(
                OcrLine(
                    text=text,
                    confidence=float(score),
                    bbox=(min(xs), min(ys), max(xs), max(ys)),
                    quad=quad,
                    word_boxes=words,
                )
            )
        lines.sort(key=lambda l: (l.bbox[1], l.bbox[0]))
        return lines


def _iter_words(entry):
    """RapidOCRのword_resultsは行ごとに (word, score, box) のタプル列。"""
    if entry is None:
        return
    for item in entry:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            yield str(item[0]), item[1], item[2]


_JP_CHAR = r"[　-ヿ㐀-鿿豈-﫿＀-￯]"


def _line_binary(line: OcrLine, image: np.ndarray):
    """行bbox周辺の二値インク画像を返す (binary, cx0, cy0)。

    Otsuで極性を決めた後、「背景とインクピーク輝度の中点」でしきい直す。
    グロー(光彩)はピークより暗いので落ち、字画コアだけが残る。
    グロー付き行での文字毎高さ計測・スペース実在検証を成立させるための処置。
    """
    x0, y0, x1, y1 = [int(round(v)) for v in line.bbox]
    h, w = image.shape[:2]
    pad = max(2, int((y1 - y0) * 0.15))
    cy0, cy1 = max(0, y0 - pad), min(h, y1 + pad)
    cx0, cx1 = max(0, x0 - pad), min(w, x1 + pad)
    if cy1 - cy0 < 4 or cx1 - cx0 < 4:
        return None
    gray = cv2.cvtColor(image[cy0:cy1, cx0:cx1], cv2.COLOR_BGR2GRAY)
    _t, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if np.count_nonzero(binary) > binary.size * 0.5:
        binary = cv2.bitwise_not(binary)
    ink_values = gray[binary > 0].astype(np.float64)
    bg_values = gray[binary == 0].astype(np.float64)
    if len(ink_values) >= 32 and len(bg_values) >= 32:
        bg = float(np.median(bg_values))
        ink_med = float(np.median(ink_values))
        text_brighter = ink_med > bg
        peak = float(np.percentile(ink_values, 92 if text_brighter else 8))
        midpoint = (bg + peak) / 2.0
        refined = binary.copy()
        if text_brighter:
            refined[gray < midpoint] = 0
        else:
            refined[gray > midpoint] = 0
        if np.count_nonzero(refined) >= max(16, int(np.count_nonzero(binary) * 0.10)):
            binary = refined
    return binary, cx0, cy0


def char_ink_heights(line: OcrLine, image: np.ndarray) -> list[float]:
    """文字(単語)ボックスごとのインク高さを画像から実測する。"""
    boxes = line.word_boxes
    if not boxes:
        return []
    packed = _line_binary(line, image)
    if packed is None:
        return []
    binary, cx0, _cy0 = packed
    heights: list[float] = []
    for _word, (bx0, _by0, bx1, _by1) in boxes:
        ix0 = max(0, int(bx0) - cx0)
        ix1 = min(binary.shape[1], int(round(bx1)) - cx0)
        if ix1 - ix0 < 1:
            heights.append(0.0)
            continue
        ys, _xs = np.nonzero(binary[:, ix0:ix1])
        heights.append(float(ys.max() - ys.min() + 1) if len(ys) else 0.0)
    return heights


def split_mixed_sizes(line: OcrLine, image: np.ndarray) -> list[OcrLine]:
    """強調数字の混在行を分割する。

    例: 「国家投資規模:FY2027 **15.0**億円 → 5YEARS **50**億円」のように
    大きな数字が文中に埋め込まれるパターン。連続する数字列のインク高さが
    本文(非数字)中央値の1.30倍以上のときのみ、その数字列を独立要素に切り出す。
    条件が数字列に限定されているため、欧文小文字と漢字の自然な高さ差などで
    誤分割することがない。
    """
    boxes = line.word_boxes
    if len(boxes) < 4:
        return [line]
    if (line.bbox[3] - line.bbox[1]) < 28:
        return [line]
    heights = char_ink_heights(line, image)
    if len(heights) != len(boxes):
        return [line]

    def is_digit_like(word: str) -> bool:
        return bool(word) and all(c in "0123456789.," for c in word)

    # 非数字文字の基準高
    base_heights = sorted(h for (w, _b), h in zip(boxes, heights)
                          if h > 3 and not is_digit_like(w))
    if len(base_heights) < 3:
        return [line]
    base_h = base_heights[len(base_heights) // 2]

    # 連続する数字列(ラン)を検出。「FY2027」の後に「15.0」が続くように
    # 小さい数字列と大きい数字列が連結する場合があるため、ラン内部でも
    # 隣接数字の高さが1.35倍以上跳ねる位置で細分してから評価する。
    def subdivide(start: int, end: int) -> list[tuple[int, int]]:
        parts = []
        s = start
        prev_h = None
        for k in range(start, end):
            h = heights[k]
            if h > 3:
                if prev_h is not None and max(h, prev_h) / max(min(h, prev_h), 1.0) >= 1.35:
                    parts.append((s, k))
                    s = k
                prev_h = h
        parts.append((s, end))
        return parts

    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(boxes):
        if is_digit_like(boxes[i][0]):
            j = i
            while j < len(boxes) and is_digit_like(boxes[j][0]):
                j += 1
            for s, e in subdivide(i, j):
                digits = sum(len(boxes[k][0].replace(".", "").replace(",", ""))
                             for k in range(s, e))
                run_heights = sorted(h for k in range(s, e) if (h := heights[k]) > 3)
                if digits < 2 or not run_heights:
                    continue
                run_h = run_heights[len(run_heights) // 2]
                if run_h < base_h * 1.30:
                    continue
                # 妥当性: ランの両端の外側に「高さの段差」が実在すること。
                # グローで計測が潰れた区間では段差が出ないため、安全に不採用となる。
                def edge_ok(index: int, direction: int) -> bool:
                    k = index
                    while 0 <= k < len(boxes):
                        if heights[k] > 3:
                            return run_h / max(heights[k], 1.0) >= 1.20
                        k += direction
                    return True  # 行端はOK
                if edge_ok(s - 1, -1) and edge_ok(e, 1):
                    runs.append((s, e))
            i = j
        else:
            i += 1
    if not runs:
        return [line]

    # ラン境界で分割
    cuts: list[tuple[int, int]] = []
    cursor = 0
    for start, end in runs:
        if start > cursor:
            cuts.append((cursor, start))
        cuts.append((start, end))
        cursor = end
    if cursor < len(boxes):
        cuts.append((cursor, len(boxes)))

    result: list[OcrLine] = []
    for start, end in cuts:
        seg_boxes = boxes[start:end]
        text = "".join(word for word, _b in seg_boxes)
        if not text.strip():
            continue
        xs0 = min(b[0] for _w, b in seg_boxes)
        xs1 = max(b[2] for _w, b in seg_boxes)
        result.append(OcrLine(
            text=text,
            confidence=line.confidence,
            bbox=(xs0, line.bbox[1], xs1, line.bbox[3]),
            quad=line.quad,
            word_boxes=seg_boxes,
        ))
    return result if len(result) >= 2 else [line]


def restore_spaces(line: OcrLine, image: np.ndarray | None = None) -> str:
    """認識テキストのスペースを保持しつつ、単語ボックスのギャップから
    「落ちたスペース」だけを追加復元する。

    例: 'Alpha×Beta /2030 Vision' → 'Alpha × Beta / 2030 Vision'
        '日本・アジア次世代…' → '日本・アジア 次世代…' (視覚的な空きがある場合)

    単語ボックスは検出アーティファクトで偽ギャップを含むことがあるため、
    imageが与えられた場合は「ギャップ区間に実際に空白列が連続しているか」を
    画像で検証してから挿入する。
    """
    import re

    if len(line.word_boxes) < 2:
        return line.text
    text = line.text
    jp = re.compile(_JP_CHAR)

    # 画像検証の準備 (列ごとのインク有無)
    column_has_ink = None
    binary_x0 = 0
    if image is not None:
        packed = _line_binary(line, image)
        if packed is not None:
            binary, binary_x0, _cy0 = packed
            tolerance = max(1, int(binary.shape[0] * 0.04))
            column_has_ink = (binary > 0).sum(axis=0) > tolerance

    def gap_is_truly_empty(gx0: float, gx1: float, need: float) -> bool:
        """[gx0,gx1]のキャンバスx区間に、長さneed以上の連続空白列があるか。"""
        if column_has_ink is None:
            return True  # 画像なしでは検証をスキップ(従来動作)
        i0 = max(0, int(gx0) - binary_x0 - 1)
        i1 = min(len(column_has_ink), int(round(gx1)) - binary_x0 + 1)
        if i1 <= i0:
            return False
        run = best = 0
        for has_ink in column_has_ink[i0:i1]:
            run = 0 if has_ink else run + 1
            best = max(best, run)
        return best >= need
    # 基準0: 行の文字高(em代理)。単語ボックス高の中央値。
    box_heights = sorted(b[3] - b[1] for _w, b in line.word_boxes)
    em_h = box_heights[len(box_heights) // 2] if box_heights else 0.0
    # 基準1: 全角文字の送り幅(広い側)の中央値。句読点等の狭い字形は除外。
    jp_widths = sorted(
        b[2] - b[0] for w, b in line.word_boxes if len(w) == 1 and jp.search(w)
    )
    jp_char_w = None
    if jp_widths:
        wide = [v for v in jp_widths if v > jp_widths[-1] * 0.6]
        jp_char_w = wide[len(wide) // 2] if wide else jp_widths[len(jp_widths) // 2]
    # 基準2: 日本語同士の境界ギャップの中央値。トラッキング(字間広め)の行でも
    # 「その行の通常字間」を基準にできるため、実スペースだけが外れ値になる。
    jp_gaps = []
    prev_box = None
    prev_w = ""
    for w, b in line.word_boxes:
        if prev_box is not None and jp.search(prev_w[-1:]) and jp.search(w[:1]):
            jp_gaps.append(max(b[0] - prev_box[2], 0.0))
        prev_box = b
        prev_w = w
    jp_gaps.sort()
    jp_median_gap = jp_gaps[len(jp_gaps) // 2] if jp_gaps else 0.0

    pieces: list[str] = []
    ti = 0
    prev_x1: float | None = None
    prev_word = ""
    for word, (x0, _y0, x1, _y1) in line.word_boxes:
        if not word:
            continue
        # 認識テキスト側の対応位置へ進める(スペースをまたぐ)
        space_in_text = False
        while ti < len(text) and text[ti] == " ":
            space_in_text = True
            ti += 1
        if text[ti: ti + len(word)] != word:
            return line.text  # 対応が取れない場合は元テキストをそのまま使う
        ti += len(word)

        if prev_x1 is not None:
            gap = x0 - prev_x1
            boundary_jp = bool(jp.search(prev_word[-1:])) and bool(jp.search(word[:1]))
            # 認識テキストのスペースは常に維持。ギャップからの追加挿入は
            # 「日本語同士の境界で、行内通常ギャップから大きく外れる」場合のみ。
            insert = space_in_text
            if not insert and boundary_jp and jp_char_w:
                if gap > max(2.5 * jp_median_gap, 0.45 * jp_char_w, 4.0):
                    # 偽ギャップ(検出ボックスのずれ)対策: 画像上で本当に
                    # 空白列が連続しているかを検証してから挿入する
                    insert = gap_is_truly_empty(prev_x1, x0, 0.30 * jp_char_w)
            elif not insert and not boundary_jp and em_h > 6:
                # 片側以上が非和文(欧文語・数字・×等)の境界: 欧文の語間
                # スペース(約0.25em)相当のギャップがあれば復元する。
                # 単語ボックスは欧文では語単位なので、語内の字間トラッキングで
                # 誤挿入する余地はない。偽ギャップ対策の画像検証は必須。
                if gap > max(2.2 * jp_median_gap, 0.24 * em_h, 3.5):
                    insert = gap_is_truly_empty(prev_x1, x0, 0.16 * em_h)
            if insert:
                pieces.append(" ")
        pieces.append(word)
        prev_x1 = x1
        prev_word = word
    return "".join(pieces)


_CIRCLE_LINE_RE = None


def fix_circle_glyphs(text: str, line: OcrLine, image: np.ndarray) -> str:
    """丸記号のみの行(評価表の◎○●等)の字形をインク形状から確定する。

    OCRは ◎/○/● をラテン文字 O/0/o 等として返しがちで、そのまま欧文
    フォントで描画すると字形も大きさも崩れる。行全体が丸様の文字だけで
    構成される場合に限り、グリフごとの輪郭の入れ子構造を実測して
    ◎(二重丸=連結成分2以上) / ○(単一輪+穴) / ●(塗り) へ置換する。
    判定はすべて画像計測で、デザイン固有の仮定は持たない。
    """
    import re
    global _CIRCLE_LINE_RE
    if _CIRCLE_LINE_RE is None:
        _CIRCLE_LINE_RE = re.compile(r"^[O0oQCcDU◎○●〇@\s]+$")
    stripped = text.replace(" ", "")
    if len(stripped) < 1 or not _CIRCLE_LINE_RE.match(text):
        return text
    packed = _line_binary(line, image)
    if packed is None:
        return text
    binary, _bx0, _by0 = packed
    h = binary.shape[0]
    # 空白列でグリフ分割
    col_ink = (binary > 0).sum(axis=0) > 0
    groups: list[tuple[int, int]] = []
    start = None
    for i, v in enumerate(col_ink):
        if v and start is None:
            start = i
        elif not v and start is not None:
            groups.append((start, i))
            start = None
    if start is not None:
        groups.append((start, len(col_ink)))
    glyphs = [(a, b) for a, b in groups if (b - a) >= h * 0.30]
    if not glyphs:
        return text
    out = []
    for a, b in glyphs:
        sub = (binary[:, a:b] > 0).astype(np.uint8)
        # 丸様でない(縦横比が極端)グリフが混ざる行は対象外
        gh_rows = np.nonzero(sub.sum(axis=1))[0]
        if len(gh_rows) == 0:
            return text
        gh = gh_rows[-1] - gh_rows[0] + 1
        if not (0.6 <= (b - a) / max(gh, 1) <= 1.7):
            return text
        n_comp, _labels = cv2.connectedComponents(sub, connectivity=8)
        inv = (sub == 0).astype(np.uint8)
        n_inv, _l2, stats2, _c2 = cv2.connectedComponentsWithStats(inv, connectivity=4)
        holes = 0
        sh, sw = sub.shape
        for lab in range(1, n_inv):
            x, y, w2, h2, _area = stats2[lab]
            if x > 0 and y > 0 and x + w2 < sw and y + h2 < sh:
                holes += 1
        if n_comp - 1 >= 2:
            out.append("◎")
        elif holes >= 1:
            out.append("○")
        else:
            out.append("●")
    # グリフ数と認識文字数が一致しない場合、全グリフが同一記号なら
    # 認識文字数に合わせて展開(接触による分割不良の救済)。それ以外は保守的に維持。
    if len(out) != len(stripped):
        if len(set(out)) == 1:
            return out[0] * len(stripped)
        return text
    return "".join(out)


def is_warped(line: OcrLine, image: np.ndarray) -> bool:
    """アーチ状・波状に変形したテキストか(リング上の飾り文字等)。

    認識段の単語ボックスは整流済みで弧の情報を持たないため、
    画像の列ごとのインク中心を直接測る。列を12区画に分けて各区画の
    インク中心の中央値を取り、線形成分(傾き)を除いた残差の振れ幅が
    グリフ高の0.6倍を超えたら「変形」。直線ボックスでは再現できないため
    画像のまま保持するのが正しい。
    """
    packed = _line_binary(line, image)
    if packed is None:
        return False
    binary, _cx0, _cy0 = packed
    h, w = binary.shape
    if w < 40:
        return False
    n_bins = 12
    centers = []
    extents = []
    for i in range(n_bins):
        x0 = w * i // n_bins
        x1 = w * (i + 1) // n_bins
        ys, _xs = np.nonzero(binary[:, x0:x1])
        if len(ys) < 4:
            continue
        centers.append((x0 + x1) / 2)
        extents.append(float(ys.max() - ys.min() + 1))
        centers[-1] = ((x0 + x1) / 2, float(ys.mean()))
    if len(centers) < 6:
        return False
    xs = np.array([c[0] for c in centers])
    ys = np.array([c[1] for c in centers])
    glyph_h = float(np.median(extents))
    if glyph_h <= 4:
        return False
    coeffs = np.polyfit(xs, ys, 1)
    residuals = ys - np.polyval(coeffs, xs)
    return float(residuals.max() - residuals.min()) > glyph_h * 0.6
