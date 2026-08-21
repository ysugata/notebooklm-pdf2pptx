"""インストール済みフォントの列挙と、PILレンダリング・メトリクス取得。

全ての位置・サイズ解決は「実際にPPTXが参照するフォントファイル」を
PIL(FreeType)で描画した実測値に基づく。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from fontTools.ttLib import TTCollection, TTFont
from PIL import Image, ImageDraw, ImageFont


def _system_font_dirs() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        dirs = [
            Path("/System/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"),
            Path("/Library/Fonts"),
            home / "Library/Fonts",
        ]
        # macOSのオンデマンドフォント資産(BIZ UDGothic等)
        assets = Path("/System/Library/AssetsV2")
        if assets.is_dir():
            dirs.extend(p for p in assets.glob("com_apple_MobileAsset_Font*/**/AssetData") if p.is_dir())
        return dirs
    if os.name == "nt":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        return [
            windir / "Fonts",
            home / "AppData/Local/Microsoft/Windows/Fonts",
        ]
    return [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        home / ".fonts",
        home / ".local/share/fonts",
    ]


RIBBI = ("Regular", "Bold", "Italic", "Bold Italic")


@dataclass(frozen=True)
class FontFace:
    family: str          # 検索用ファミリー名 (name ID 16 または 1)
    subfamily: str       # Regular / Bold / W6 / Condensed Medium ...
    weight: int          # usWeightClass
    italic: bool
    path: str
    index: int           # TTC内インデックス
    typeface: str        # PPTXに書くバインディング名
    bind_bold: bool      # PPTX側で b=1 を立てて到達するフェイスか

    @property
    def is_bold(self) -> bool:
        return self.weight >= 600


def binding_name(name1: str, name2: str, style: str | None = None) -> tuple[str, bool]:
    """PowerPointが解決できる typeface 名と bold フラグを決める。

    - RIBBIスタイル(Regular/Bold/...)はファミリー名+フラグで到達する。
      ID1が 'Family Bold' のような形ならスタイル部分を剥がす。
    - 非RIBBI(W6, Heavy, Condensed Medium等)はフルネームで到達する
      (bold フラグは立てない。立てると疑似ボールドが二重にかかる)。

    styleにはタイポグラフィックサブファミリー(ID17)を渡す。ID2が'Bold'でも
    ID17が'Heavy'等ならそちらを優先して非RIBBI扱いにする。
    """
    name2 = (name2 or "Regular").strip()
    effective = (style or name2).strip()
    if effective in RIBBI:
        family = name1
        suffix = " " + effective
        if effective != "Regular" and family.endswith(suffix):
            family = family[: -len(suffix)]
        return family, "Bold" in effective
    # 非RIBBI: フルネームで到達する。ID1に既に含まれるスタイル語は重複させない
    # (例: 'Avenir Next Condensed Heavy' + 'Heavy Italic' → '... Heavy Italic')
    target = name1
    for token in effective.split():
        if not target.lower().endswith(" " + token.lower()) and \
                target.lower() != token.lower():
            target = f"{target} {token}"
    return target, False


class FontLibrary:
    def __init__(self, extra_dirs: list[Path] | None = None) -> None:
        self.faces: dict[str, list[FontFace]] = {}
        dirs = _system_font_dirs() + list(extra_dirs or [])
        seen: set[str] = set()
        for directory in dirs:
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                key = str(path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                self._register_file(path)

    def _register_file(self, path: Path) -> None:
        suffix = path.suffix.lower()
        try:
            if suffix in (".ttf", ".otf"):
                self._register_font(TTFont(str(path), fontNumber=0, lazy=True), path, 0)
            elif suffix == ".ttc":
                collection = TTCollection(str(path), lazy=True)
                for index, font in enumerate(collection.fonts):
                    self._register_font(font, path, index)
        except Exception:
            pass

    def _register_font(self, tt: TTFont, path: Path, index: int) -> None:
        try:
            name = tt["name"]
            name1 = name.getDebugName(1)
            name2 = name.getDebugName(2) or "Regular"
            family = name.getDebugName(16) or name1
            subfamily = name.getDebugName(17) or name2
            if not family or not name1:
                return
            weight = 400
            italic = False
            if "OS/2" in tt:
                weight = int(tt["OS/2"].usWeightClass)
                italic = bool(tt["OS/2"].fsSelection & 0x01)
            # イタリック検出はOS/2フラグだけでは漏れる(Apple製フォント等は
            # macStyleやサブファミリー名にのみ現れることがある)
            if "head" in tt:
                italic = italic or bool(tt["head"].macStyle & 0x02)
            style_names = f"{name2} {name.getDebugName(17) or ''}".lower()
            italic = italic or ("italic" in style_names) or ("oblique" in style_names)
            typeface, bind_bold = binding_name(name1, name2, name.getDebugName(17))
            face = FontFace(family, subfamily, weight, italic, str(path), index,
                            typeface, bind_bold)
            self.faces.setdefault(family, []).append(face)
            if typeface != family:
                self.faces.setdefault(typeface, []).append(face)
        except Exception:
            pass

    def find(self, family: str) -> list[FontFace]:
        if family in self.faces:
            return self.faces[family]
        lowered = family.casefold()
        for key, value in self.faces.items():
            if key.casefold() == lowered:
                return value
        return []

    def face(self, family: str, bold: bool = False, italic: bool = False) -> FontFace | None:
        faces = [f for f in self.find(family) if f.italic == italic]
        if not faces:
            faces = self.find(family)
        if not faces:
            return None
        target = 700 if bold else 400
        return min(faces, key=lambda f: abs(f.weight - target))

    def available(self, families: tuple[str, ...] | list[str]) -> list[str]:
        return [f for f in families if self.find(f)]


@lru_cache(maxsize=256)
def _load_pil_font(path: str, index: int, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size, index=index)


@dataclass
class RenderMetrics:
    """基準サイズで文字列を描画した実測値。すべて基準サイズのピクセル単位。"""
    ink_w: float
    ink_h: float
    ink_left: float      # 描画原点(左端)からインク左端まで
    ink_top_from_ascent: float  # アセントライン(PIL描画のy原点)からインク上端まで
    ascent: float
    descent: float
    adv_w: float         # 行全体の送り幅 (font.getlength)
    n_gaps: int          # 字間が入る隙間の数 (len(text)-1、空白を除外しない)
    ink_pixels: int      # インク画素数(太さ照合用)
    stroke_width: float  # 距離変換によるストローク幅(基準サイズpx)


REF_SIZE = 100

# 主要インク行の判定閾値。高すぎると欧文のアセンダ/ディセンダ行まで
# 削れて過大サイズ化する。グローの裾・孤立ノイズの除去が目的なので低めに。
DENSITY_ROW_THRESHOLD = 0.08


def _density_rows(binary: np.ndarray) -> tuple[int, int]:
    """インクが十分に詰まった行範囲 (top, bottom) を返す。

    行ごとのインク画素数が最大行の DENSITY_ROW_THRESHOLD 倍以上の行だけを
    「主要インク」とみなす。グローの裾やまばらなディセンダ行を除外して、
    実測とレンダリングを同じ規則で比較するために使う。
    """
    counts = (binary > 0).sum(axis=1).astype(np.float64)
    peak = counts.max()
    if peak <= 0:
        return 0, binary.shape[0] - 1
    dense = np.nonzero(counts >= peak * DENSITY_ROW_THRESHOLD)[0]
    return int(dense[0]), int(dense[-1])


@lru_cache(maxsize=4096)
def render_metrics(text: str, path: str, index: int) -> RenderMetrics | None:
    """基準サイズREF_SIZEでtextを描画し、インク実測値を返す。"""
    try:
        font = _load_pil_font(path, index, REF_SIZE)
    except Exception:
        return None
    ascent, descent = font.getmetrics()
    width = int(font.getlength(text)) + REF_SIZE * 2
    height = ascent + descent + REF_SIZE * 2
    image = Image.new("L", (max(width, 1), max(height, 1)), 0)
    draw = ImageDraw.Draw(image)
    origin = (REF_SIZE, REF_SIZE)
    draw.text(origin, text, font=font, fill=255)
    array = np.asarray(image)
    ys, xs = np.nonzero(array > 96)
    if len(xs) == 0:
        return None
    import cv2

    binary = (array > 96).astype(np.uint8) * 255
    dist = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    positive = dist[dist > 0]
    stroke = float(np.median(positive) * 2.0) if len(positive) else 1.0
    # 高さは行密度トリム値を使う (inkstats.measure_inkと同じ規則。
    # ディセンダ等のまばらな行を除いた「主要インク高」で、グロー付き
    # 実測と対称に比較できる)
    top_d, bottom_d = _density_rows(binary)
    return RenderMetrics(
        ink_w=float(xs.max() - xs.min() + 1),
        ink_h=float(bottom_d - top_d + 1),
        ink_left=float(xs.min() - origin[0]),
        ink_top_from_ascent=float(top_d - origin[1]),
        ascent=float(ascent),
        descent=float(descent),
        adv_w=float(font.getlength(text)),
        n_gaps=max(len(text) - 1, 1),
        ink_pixels=int(np.count_nonzero(array > 96)),
        stroke_width=stroke,
    )


@lru_cache(maxsize=64)
def glyph_coverage(path: str, index: int) -> frozenset:
    """フォントのcmapに存在するコードポイント集合。"""
    try:
        tt = TTFont(path, fontNumber=index, lazy=True)
        cmap = tt.getBestCmap()
        return frozenset(cmap.keys())
    except Exception:
        return frozenset()


def missing_glyphs(text: str, path: str, index: int) -> str:
    coverage = glyph_coverage(path, index)
    if not coverage:
        return ""
    return "".join(sorted({c for c in text if not c.isspace() and ord(c) not in coverage}))


@lru_cache(maxsize=64)
def line_metrics_units(path: str, index: int) -> dict:
    """PowerPointの行高計算に使うフォントメトリクス(unitsPerEm正規化前の生値)。"""
    tt = TTFont(path, fontNumber=index, lazy=True)
    upem = tt["head"].unitsPerEm
    result = {"upem": upem}
    if "hhea" in tt:
        result["hhea_ascent"] = tt["hhea"].ascent
        result["hhea_descent"] = -tt["hhea"].descent  # 正の値に
        result["hhea_linegap"] = tt["hhea"].lineGap
    if "OS/2" in tt:
        os2 = tt["OS/2"]
        result["win_ascent"] = os2.usWinAscent
        result["win_descent"] = os2.usWinDescent
        result["typo_ascent"] = os2.sTypoAscender
        result["typo_descent"] = -os2.sTypoDescender
        result["typo_linegap"] = os2.sTypoLineGap
        result["use_typo"] = bool(os2.fsSelection & 0x80)
    return result
