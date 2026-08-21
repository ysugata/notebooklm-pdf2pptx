"""OFLフォントのダウンロード: 照合・出力品質を大きく上げる推奨フォント群。

NotebookLM/Geminiが生成するスライド画像のタイポグラフィは
Noto Sans JP(和文)・Oswald等のコンデンス欧文に近く、これらが
あると照合誤差が大幅に減る。全てSIL Open Font Licenseで、
PPTXへの埋め込みも許諾されている。

使い方: .venv/bin/python fetch_fonts.py
ダウンロード先: fonts/  (git管理外)
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

FONTS_DIR = Path(__file__).resolve().parent / "fonts"

# バージョン固定URL (再現性のためタグ/バージョンを固定)
NOTO_TAG = "Sans2.004"
FONTSOURCE = "https://cdn.jsdelivr.net/fontsource/fonts"

FONT_URLS = {
    # 和文: Noto Sans JP (サブセットOTF, 約5MB/ウェイト)
    "NotoSansJP-Regular.otf": f"https://raw.githubusercontent.com/notofonts/noto-cjk/{NOTO_TAG}/Sans/SubsetOTF/JP/NotoSansJP-Regular.otf",
    "NotoSansJP-Medium.otf": f"https://raw.githubusercontent.com/notofonts/noto-cjk/{NOTO_TAG}/Sans/SubsetOTF/JP/NotoSansJP-Medium.otf",
    "NotoSansJP-Bold.otf": f"https://raw.githubusercontent.com/notofonts/noto-cjk/{NOTO_TAG}/Sans/SubsetOTF/JP/NotoSansJP-Bold.otf",
    "NotoSansJP-Black.otf": f"https://raw.githubusercontent.com/notofonts/noto-cjk/{NOTO_TAG}/Sans/SubsetOTF/JP/NotoSansJP-Black.otf",
    # 欧文コンデンス: Oswald (fontsource静的TTF, latinサブセット)
    "Oswald-400.ttf": f"{FONTSOURCE}/oswald@5.2.5/latin-400-normal.ttf",
    "Oswald-500.ttf": f"{FONTSOURCE}/oswald@5.2.5/latin-500-normal.ttf",
    "Oswald-600.ttf": f"{FONTSOURCE}/oswald@5.2.5/latin-600-normal.ttf",
    "Oswald-700.ttf": f"{FONTSOURCE}/oswald@5.2.5/latin-700-normal.ttf",
    # 欧文コンデンス: Roboto Condensed
    "RobotoCondensed-400.ttf": f"{FONTSOURCE}/roboto-condensed@5.2.8/latin-400-normal.ttf",
    "RobotoCondensed-700.ttf": f"{FONTSOURCE}/roboto-condensed@5.2.8/latin-700-normal.ttf",
    # 和文プロポーショナルかな: BIZ UDPGothic (Windows標準・OFL・TTF)
    "BIZUDPGothic-Regular.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/bizudpgothic/BIZUDPGothic-Regular.ttf",
    "BIZUDPGothic-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/bizudpgothic/BIZUDPGothic-Bold.ttf",
    # 等幅: Roboto Mono (コード風ラベル・ターミナル風デッキ用)
    "RobotoMono-400.ttf": f"{FONTSOURCE}/roboto-mono@5.2.7/latin-400-normal.ttf",
    "RobotoMono-700.ttf": f"{FONTSOURCE}/roboto-mono@5.2.7/latin-700-normal.ttf",
    # 和文丸ゴシック: M PLUS Rounded 1c (NotebookLM系の強調テキストに頻出)
    "MPLUSRounded1c-Regular.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/mplusrounded1c/MPLUSRounded1c-Regular.ttf",
    "MPLUSRounded1c-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/mplusrounded1c/MPLUSRounded1c-Bold.ttf",
    "MPLUSRounded1c-Black.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/mplusrounded1c/MPLUSRounded1c-Black.ttf",
}


def user_font_dir() -> Path | None:
    import sys

    if sys.platform == "darwin":
        return Path.home() / "Library/Fonts"
    if sys.platform.startswith("win"):
        import os
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts"
    return Path.home() / ".local/share/fonts"


def main() -> None:
    import argparse
    import shutil

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true",
                        help="OSのユーザーフォントとしてもインストールする "
                             "(PowerPoint/Keynote等のレンダラから見えるようにする)")
    args = parser.parse_args()

    FONTS_DIR.mkdir(exist_ok=True)
    for filename, url in FONT_URLS.items():
        destination = FONTS_DIR / filename
        if destination.is_file() and destination.stat().st_size > 10000:
            print(f"skip (exists): {filename}")
            continue
        print(f"downloading {filename} ...", flush=True)
        with urllib.request.urlopen(url, timeout=120) as response:
            destination.write_bytes(response.read())
    print(f"完了: {FONTS_DIR}")

    if args.install:
        target = user_font_dir()
        if target is None or not target.parent.exists():
            print("ユーザーフォントディレクトリが見つかりません。手動でインストールしてください。")
        else:
            target.mkdir(parents=True, exist_ok=True)
            for font in FONTS_DIR.iterdir():
                if font.suffix.lower() in (".ttf", ".otf"):
                    shutil.copy2(font, target / font.name)
            print(f"インストール完了: {target}")
    else:
        print("PPTXを開くPC(このPC含む)にはフォントのインストールが必要です: --install")


if __name__ == "__main__":
    main()
