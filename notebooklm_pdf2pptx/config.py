from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 和文・欧文それぞれの候補フォント。先頭ほど優先だが、最終選択は
# 実測インク形状との照合スコアで決まる(solver.choose_font)。
# 注意: Gen Shin Gothic系は名前解決がレンダラ依存で不安定
# (QuickLook/LibreOfficeが非プロポーショナル同族へ誤解決する実測あり)のため除外。
JP_FONT_CANDIDATES = (
    "Noto Sans JP",
    "BIZ UDPGothic",
    "BIZ UDGothic",
    "Rounded Mplus 1c",
    "Hiragino Sans",
    "Hiragino Kaku Gothic ProN",
    "Yu Gothic",
    "Meiryo",
)
LATIN_FONT_CANDIDATES = (
    "Oswald",
    "Roboto Condensed",
    "Roboto Mono",
    "Arial Narrow",
    "Avenir Next Condensed",
    "DIN Condensed",
    "Futura",
    "Helvetica Neue",
    "Arial",
)

# PPTX側でOS間フォールバックさせる既定(和文)。選択フォントが相手先に無い場合の保険。
JP_FALLBACK_CHAIN = ("Noto Sans JP", "Yu Gothic", "Hiragino Sans", "Meiryo")

# ポータブルフォント: 自由に配布・インストールでき(OFL / Windows標準)、
# RIBBI命名が素直でPowerPoint/LibreOffice/Keynoteのどれでも同名で解決できる。
# fetch_fonts.py --install で導入されるセット + Windows標準のBIZ UD。
PORTABLE_FONT_FAMILIES = frozenset({
    "Noto Sans JP",
    "Oswald",
    "Roboto Condensed",
    "Roboto Mono",
    "Rounded Mplus 1c",
    "BIZ UDPGothic",
    "BIZ UDGothic",
})


@dataclass
class Settings:
    # 入出力
    work_dir: Path = PROJECT_ROOT / "work"
    fonts_dir: Path = PROJECT_ROOT / "fonts"

    # acquire
    render_scale: float = 2.0        # 全面画像でないページのレンダリング倍率 (72dpi基準)
    full_page_image_coverage: float = 0.90  # これ以上を占める1枚画像なら「全面画像ページ」

    # ocr
    ocr_upscale: float = 2.0         # OCR前のLanczos拡大率
    ocr_model_type: str = "small"    # small(高速,日本語88.2%) | medium(日本語90.5%)
    min_confidence: float = 0.50     # これ未満は要素化しない(ノイズ扱い)
    review_confidence: float = 0.85  # これ未満は review レポートに載せる

    # measure / solve
    gradient_min_delta: float = 28.0  # 上下の色差(RGB距離)がこれ以上ならグラデーション文字
    max_char_spacing_pt: float = 12.0  # spcの絶対値上限(異常値ガード)
    # 負字間(詰め)の下限 (emに対する比)。幅一致より可読性を優先する境界。
    # これを超える詰めは文字が重なって読めなくなるため適用しない。
    max_negative_spacing_em: float = 0.08
    # 正字間(トラッキング)がこれ(em比)を超える解は「観測インク幅の膨張」の
    # 疑いがあるため、自然字間のテンプレートも並行照合してNCCに裁定させる。
    max_positive_spacing_em: float = 0.11
    # 色ラン分割: 行内の部分強調(金色・赤色のアクセント)を1行1色に
    # 潰さないための一般則。細字のエッジ減光(白がグレーに測れる)で
    # 偽分割しないよう、輝度を正規化したクロマ距離と輝度比の2軸で判定する。
    color_run_chroma_delta: float = 25.0   # 輝度正規化クロマ距離(160スケール)の閾値
    color_run_lum_ratio: float = 2.2       # 輝度比がこれ以上なら色変化とみなす
    # 非ポータブルフォントのスコアペナルティ。0.18で「形状がよほど良い場合のみ」
    # システム固有フォントを許す。0にするとローカル最良形状を常に選ぶ。
    non_portable_penalty: float = 0.18

    # remove
    inpaint: str = "auto"            # auto | lama | classic | flat
    flat_std_threshold: float = 4.0  # 背景リングの標準偏差がこれ未満ならフラット扱い
    lama_venv: Path = PROJECT_ROOT / ".venv-iopaint"

    # compose
    force_16_9: bool = True          # ページアスペクトが16:9近傍なら16:9へ正規化
    jpeg_quality: int = 92           # 背景画像の再圧縮品質

    # qa
    qa_enabled: bool = True

    pages: list[int] = field(default_factory=list)  # 空=全ページ
