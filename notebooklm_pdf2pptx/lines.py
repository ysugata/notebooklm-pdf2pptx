"""行のグルーピング(段落化)と整列検出。

同一スタイル・等ピッチの縦並び行を1つのテキストボックスへまとめ、
行間は実測ピッチ(spcPtsで正確指定)を使う。整列は左端/中央/右端の
揃い具合(分散)から決める。
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from .solver import SolvedStyle


@dataclass
class SolvedLine:
    text: str
    style: SolvedStyle
    ink_bbox: tuple[int, int, int, int]
    confidence: float
    source: str = "ocr"      # ocr | pdf
    ink: object | None = None  # InkStats (ブロック統一の再解決用、シリアライズしない)
    glow: dict | None = None   # {radius_pt, color} グロー(光彩)検出結果
    color_runs: list | None = None  # [[start,end,[r,g,b]],...] 行内の色ラン(部分強調)


@dataclass
class TextBlock:
    lines: list[SolvedLine]
    align: str               # left | center | right
    pitch_px: float          # ベースライン間隔 (単一行は0)

    @property
    def ink_bbox(self) -> tuple[int, int, int, int]:
        xs0, ys0, xs1, ys1 = zip(*(l.ink_bbox for l in self.lines))
        return (min(xs0), min(ys0), max(xs1), max(ys1))


def _same_style(a: SolvedLine, b: SolvedLine) -> bool:
    # 太さ(weight)は判定しない: 同一ブロック内の揺れは後段で統一する
    if a.style.face.family != b.style.face.family:
        return False
    # サイズ許容は広め: グロー分離の揺らぎで同一サイズが±15%程度ぶれるため。
    # グループ化後にブロック内でサイズを統一する。
    size = max(a.style.size_pt, b.style.size_pt, 1.0)
    if abs(a.style.size_pt - b.style.size_pt) / size > 0.18:
        return False
    ca, cb = a.style.color, b.style.color
    if sum((x - y) ** 2 for x, y in zip(ca, cb)) ** 0.5 > 60:
        return False
    return True


def _aligned(a: SolvedLine, b: SolvedLine, tol: float) -> bool:
    ax0, _, ax1, _ = a.ink_bbox
    bx0, _, bx1, _ = b.ink_bbox
    if abs(ax0 - bx0) <= tol or abs(ax1 - bx1) <= tol:
        return True
    if abs((ax0 + ax1) / 2 - (bx0 + bx1) / 2) <= tol:
        return True
    return False


def group_lines(lines: list[SolvedLine], canvas_w: int) -> list[TextBlock]:
    ordered = sorted(lines, key=lambda l: (l.style.baseline_y_px, l.ink_bbox[0]))
    blocks: list[list[SolvedLine]] = []
    for line in ordered:
        target = None
        for block in blocks:
            last = block[-1]
            size_px = max(last.ink_bbox[3] - last.ink_bbox[1], 8)
            pitch = line.style.baseline_y_px - last.style.baseline_y_px
            if pitch <= 0 or pitch > size_px * 2.2:
                continue
            if not _same_style(last, line):
                continue
            if not _aligned(last, line, tol=size_px * 0.5):
                continue
            # 水平に重ならない行(横並びの別カラム)は結合しない
            lx0, _, lx1, _ = last.ink_bbox
            cx0, _, cx1, _ = line.ink_bbox
            overlap = min(lx1, cx1) - max(lx0, cx0)
            if overlap < min(lx1 - lx0, cx1 - cx0) * 0.30:
                continue
            # 既存グループのピッチと矛盾しないか
            if len(block) >= 2:
                pitches = [block[i + 1].style.baseline_y_px - block[i].style.baseline_y_px
                           for i in range(len(block) - 1)]
                if abs(pitch - median(pitches)) > max(2.0, median(pitches) * 0.12):
                    continue
            target = block
            break
        if target is not None:
            target.append(line)
        else:
            blocks.append([line])

    return [_finalize(block, canvas_w) for block in blocks]


def _finalize(block: list[SolvedLine], canvas_w: int) -> TextBlock:
    if len(block) == 1:
        line = block[0]
        x0, _, x1, _ = line.ink_bbox
        center = (x0 + x1) / 2
        align = "center" if abs(center - canvas_w / 2) <= canvas_w * 0.012 else "left"
        return TextBlock(lines=block, align=align, pitch_px=0.0)

    lefts = [l.ink_bbox[0] for l in block]
    rights = [l.ink_bbox[2] for l in block]
    centers = [(l.ink_bbox[0] + l.ink_bbox[2]) / 2 for l in block]

    def spread(values: list[float]) -> float:
        return max(values) - min(values)

    options = {"left": spread(lefts), "center": spread(centers), "right": spread(rights)}
    align = min(options, key=options.get)
    pitches = [block[i + 1].style.baseline_y_px - block[i].style.baseline_y_px
               for i in range(len(block) - 1)]
    return TextBlock(lines=block, align=align, pitch_px=float(median(pitches)))
