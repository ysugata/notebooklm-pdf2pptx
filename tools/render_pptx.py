"""PPTXを画像へレンダリングする検証ツール。

優先順: LibreOffice (soffice) → Keynote (macOS, AppleScript)。
どちらもPowerPoint本体ではないが、テキストボックスのジオメトリ検証には十分。

使い方:
  .venv/bin/python tools/render_pptx.py output.pptx --out-dir renders/
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def render_with_soffice(pptx: Path, out_dir: Path) -> bool:
    soffice = shutil.which("soffice")
    if soffice is None and Path("/Applications/LibreOffice.app/Contents/MacOS/soffice").is_file():
        soffice = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if soffice is None:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(pptx)],
            check=True, capture_output=True, timeout=600,
        )
        pdf = Path(tmp) / (pptx.stem + ".pdf")
        if not pdf.is_file():
            return False
        _pdf_to_png(pdf, out_dir)
    return True


def render_with_keynote(pptx: Path, out_dir: Path) -> bool:
    if sys.platform != "darwin":
        return False
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "render.pdf"
        script = f'''
        tell application id "com.apple.iWork.Keynote"
            set theDoc to open POSIX file "{pptx}"
            delay 1
            export theDoc to POSIX file "{pdf}" as PDF
            close theDoc without saving
        end tell
        '''
        try:
            subprocess.run(["osascript", "-e", script], check=True,
                           capture_output=True, timeout=300)
        except subprocess.CalledProcessError as error:
            print("Keynote export failed:", error.stderr.decode()[:500])
            return False
        if not pdf.is_file():
            return False
        _pdf_to_png(pdf, out_dir)
    return True


def _pdf_to_png(pdf: Path, out_dir: Path, width_px: int = 1376) -> None:
    import fitz

    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf)
    for index, page in enumerate(doc, start=1):
        scale = width_px / page.rect.width
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        pix.save(out_dir / f"render_{index:03d}.png")
    print(f"rendered {doc.page_count} pages -> {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pptx", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    pptx = args.pptx.resolve()
    if render_with_soffice(pptx, args.out_dir):
        return
    if render_with_keynote(pptx, args.out_dir):
        return
    raise SystemExit("レンダラが見つかりません (soffice / Keynote)。")


if __name__ == "__main__":
    main()
