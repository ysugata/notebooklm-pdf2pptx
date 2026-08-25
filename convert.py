"""エントリポイント: 画像スライド/PDF → 編集可能PPTX

使い方:
  .venv/bin/python convert.py "input.pdf" -o output.pptx
  .venv/bin/python convert.py slides_dir/ -o output.pptx
  .venv/bin/python convert.py "input.pdf" --pages 1,2,5 --inpaint classic
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from notebooklm_pdf2pptx.config import Settings
from notebooklm_pdf2pptx.pipeline import Converter


def parse_pages(value: str) -> list[int]:
    pages: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            pages.update(range(int(start), int(end) + 1))
        else:
            pages.add(int(part))
    return sorted(pages)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="PDF、画像ファイル、または画像ディレクトリ")
    parser.add_argument("-o", "--output", type=Path, default=Path("output.pptx"))
    parser.add_argument("--pages", type=parse_pages, default=[],
                        help="例: 1,2,5 または 1-10 (省略時は全ページ)")
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--inpaint", choices=["auto", "lama", "classic", "flat"],
                        default="auto",
                        help="auto=フラットは決定論フィル+複雑はLaMa / classic=LaMa不使用")
    parser.add_argument("--no-qa", action="store_true")
    parser.add_argument("--local-fonts", action="store_true",
                        help="ポータブルフォント優先を無効化し、このPCで形状最良の"
                             "フォントを選ぶ(他PCへ渡すと崩れる可能性)")
    parser.add_argument("--ocr-model", choices=["small", "medium"], default="small",
                        help="OCR認識モデル。medium=高精度(日本語+2.3%%)だが低速")
    parser.add_argument("--renderer-profile", choices=["powerpoint", "libreoffice"],
                        default="powerpoint",
                        help="出力を開くレンダラ(行レイアウトの実測則を切替)")
    args = parser.parse_args()

    settings = Settings()
    settings.pages = args.pages
    settings.inpaint = args.inpaint
    settings.qa_enabled = not args.no_qa
    settings.ocr_model_type = args.ocr_model
    settings.renderer_profile = args.renderer_profile
    if args.local_fonts:
        settings.non_portable_penalty = 0.0
    if args.work_dir is not None:
        settings.work_dir = args.work_dir
    settings.work_dir.mkdir(parents=True, exist_ok=True)

    (settings.work_dir / "convert_meta.json").write_text(
        __import__("json").dumps({
            "input": str(args.input.resolve()),
            "output": str(args.output.resolve()),
            "argv": ["--inpaint", args.inpaint,
                     "--renderer-profile", args.renderer_profile]},
            ensure_ascii=False), "utf-8")
    converter = Converter(settings)
    report = converter.convert(args.input.resolve(), args.output.resolve())

    # 文字化けの要判断が残っていればレポートを自動生成してブラウザで開く
    # (回答は必ず人に届ける。残ゼロなら開かない)
    try:
        fb = settings.work_dir / "feedback"
        subprocess.run([sys.executable, str(Path(__file__).parent / "tools" / "garble_prepare.py"),
                        str(args.output.resolve()), "--work-dir", str(settings.work_dir),
                        "--out-dir", str(fb)], check=True)
        subprocess.run([sys.executable, str(Path(__file__).parent / "tools" / "feedback_report.py"),
                        str(fb / "tasks.json"), "-o", str(fb / "report.html"), "--open"],
                       check=True)
    except Exception as exc:
        print(f"要判断レポートの自動生成に失敗: {exc}")

    print("\n=== 完了 ===")
    print(f"出力: {report['output']}")
    print(f"ページ: {report['pages']}  テキスト要素: {report['text_elements']}")
    if report.get("qa"):
        for page, qa in report["qa"].items():
            print(f"  page {page}: SSIM={qa['ssim']}  要確認OCR行={qa['n_review']}")
    if report.get("reviews"):
        print("低信頼OCR行があります。work/report.json と各ページの layout.json を確認してください。")


if __name__ == "__main__":
    main()
