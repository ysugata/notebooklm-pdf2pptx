"""字形類似インデックス: フォント描画から機械計算する形近字候補。

OCR誤り訂正の定石「字形類似で候補生成 × 言語検証で採用判定」
(Nagata 1998 以降の雑音チャネル構成)の候補生成側。手作りの形近字表は
カバレッジに限界があるため、フォントの全漢字グリフを描画して
字形ベクトル(インク正規化16×16)の近傍を機械計算する。

- 決定論: 同じフォントファイルからは常に同じ近傍が出る
- 資料非依存: 文字集合はフォントのcmapから取る
- 採用の安全性はここでは担保しない(textfix側の辞書語ゲートが担保)

インデックスはフォントのハッシュをキーに rules/ 配下へキャッシュされる
(git管理外)。初回構築は数十秒、以後は数ミリ秒で読み込む。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

_CACHE_DIR = Path(__file__).resolve().parent.parent / "rules"
_CJK_RANGE = (0x4E00, 0x9FFF)
_DIM = 24  # 字形ベクトル解像度


def _render_matrix(chars: list[str], font_path: str, font_index: int) -> np.ndarray:
    from PIL import Image, ImageDraw, ImageFont
    size = 96
    font = ImageFont.truetype(font_path, size, index=max(font_index, 0))
    vecs = np.zeros((len(chars), _DIM * _DIM), np.float32)
    for i, ch in enumerate(chars):
        img = Image.new("L", (size * 2, size * 2), 0)
        ImageDraw.Draw(img).text((size // 2, size // 2), ch, font=font, fill=255)
        bbox = img.getbbox()
        if bbox is None:
            continue
        glyph = img.crop(bbox).resize((_DIM, _DIM), Image.LANCZOS)
        v = np.asarray(glyph, np.float32).ravel()
        n = np.linalg.norm(v)
        if n > 0:
            vecs[i] = v / n
    return vecs


class GlyphIndex:
    """漢字1文字 → 字形の近い漢字候補(類似度順)。"""

    def __init__(self, font_path: str, font_index: int = 0) -> None:
        self._font_path = font_path
        self._font_index = font_index
        self._chars: list[str] = []
        self._charset: set[str] = set()
        self._mat: np.ndarray | None = None
        self._memo: dict[str, str] = {}
        self._load_or_build()
        self._charset = set(self._chars)

    def _load_or_build(self) -> None:
        from fontTools.ttLib import TTFont
        p = Path(self._font_path)
        key = hashlib.sha256(
            f"{p.name}:{p.stat().st_size}:{self._font_index}:v2".encode()
        ).hexdigest()[:16]
        cache = _CACHE_DIR / f"shape_index_{key}.npz"
        if cache.is_file():
            data = np.load(str(cache), allow_pickle=False)
            self._chars = list(str(data["chars"]))
            self._mat = data["mat"]
            return
        tf = TTFont(self._font_path,
                    fontNumber=self._font_index if self._font_index >= 0 else 0)
        cmap = tf.getBestCmap()
        codes = [c for c in cmap if _CJK_RANGE[0] <= c <= _CJK_RANGE[1]]
        self._chars = [chr(c) for c in sorted(codes)]
        self._mat = _render_matrix(self._chars, self._font_path, self._font_index)
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(str(cache), chars="".join(self._chars), mat=self._mat)

    def _fallback_font_for(self, ch: str) -> tuple[str, int] | None:
        """主フォント未収載の字(簡体字等)を描画できる別フォントを探す。"""
        import sys
        from fontTools.ttLib import TTFont
        cands = []
        if sys.platform == "darwin":
            cands = ["/System/Library/Fonts/Hiragino Sans GB.ttc",
                     "/System/Library/Fonts/STHeiti Medium.ttc",
                     "/Library/Fonts/Arial Unicode.ttf"]
        elif sys.platform == "win32":
            cands = ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simsun.ttc"]
        else:
            cands = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]
        for path in cands:
            try:
                tf = TTFont(path, fontNumber=0)
                if ord(ch) in tf.getBestCmap():
                    return path, 0
            except Exception:
                continue
        return None

    def neighbors(self, ch: str, k: int = 8, min_sim: float = 0.6) -> str:
        """chと字形の近い漢字を類似度順に返す(ch自身は含まない)。"""
        if ch in self._memo:
            return self._memo[ch]
        if self._mat is None or not self._chars:
            return ""
        if ch in self._charset:
            v = _render_matrix([ch], self._font_path, self._font_index)[0]
        else:
            fb = self._fallback_font_for(ch)
            if fb is None:
                self._memo[ch] = ""
                return ""
            v = _render_matrix([ch], fb[0], fb[1])[0]
        if not v.any():
            self._memo[ch] = ""
            return ""
        sims = self._mat @ v
        order = np.argsort(-sims)
        out = []
        for idx in order[: k + 4]:
            cand = self._chars[idx]
            if cand == ch or sims[idx] < min_sim:
                continue
            out.append(cand)
            if len(out) >= k:
                break
        res = "".join(out)
        self._memo[ch] = res
        return res
