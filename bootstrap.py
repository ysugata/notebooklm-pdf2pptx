"""環境構築: 仮想環境の作成と依存関係のインストール。

  python3 bootstrap.py                # 基本環境 (.venv) + OCRモデル事前取得
  python3 bootstrap.py --with-lama    # + LaMa修復環境 (.venv-iopaint)
  python3 bootstrap.py --with-fonts   # + 推奨OFLフォントのダウンロード

Python 3.10〜3.12 推奨 (onnxruntime / IOPaint の対応範囲)。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def env_python(env_dir: Path) -> Path:
    if os.name == "nt":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def create_environment(env_dir: Path, requirement_file: Path,
                       extra_index_url: str | None = None) -> Path:
    if not env_python(env_dir).is_file():
        venv.EnvBuilder(with_pip=True).create(env_dir)
    python = env_python(env_dir)
    subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    command = [str(python), "-m", "pip", "install", "-r", str(requirement_file)]
    if extra_index_url:
        command.extend(["--extra-index-url", extra_index_url])
    subprocess.run(command, check=True)
    return python


def has_nvidia_gpu() -> bool:
    import shutil

    if shutil.which("nvidia-smi") is None:
        return False
    try:
        return subprocess.run(["nvidia-smi", "-L"], capture_output=True,
                              timeout=15).returncode == 0
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-lama", action="store_true",
                        help="LaMa(IOPaint)修復環境も構築する (複雑背景の高品質テキスト除去)")
    parser.add_argument("--with-fonts", action="store_true",
                        help="推奨OFLフォント(Noto Sans JP / Oswald等)をダウンロードする")
    parser.add_argument("--cuda", action="store_true",
                        help="LaMa用にCUDA版PyTorchを強制インストール (Windows/Linux + NVIDIA)")
    parser.add_argument("--no-cuda", action="store_true",
                        help="NVIDIA GPUがあってもCPU版PyTorchを使う")
    args = parser.parse_args()

    python = create_environment(ROOT / ".venv", ROOT / "requirements.txt")

    # OCRモデルの事前ダウンロード (オフライン運用のため初回にまとめて取得)
    print("OCRモデルを事前取得中...")
    subprocess.run([str(python), "-c", (
        "from rapidocr import RapidOCR, LangRec, ModelType, OCRVersion;"
        "RapidOCR(params={'Rec.lang_type': LangRec.JAPAN,"
        "'Rec.ocr_version': OCRVersion.PPOCRV6,"
        "'Rec.model_type': ModelType.SMALL})"
    )], check=True)

    if args.with_lama:
        # Windows/LinuxのPyPI版torchはCPUビルド。NVIDIA GPUがあれば
        # CUDA版のwheelインデックスを追加して自動でGPU対応にする
        # (macOSは標準wheelにMPS対応が含まれるため不要)。
        extra_index = None
        import platform as _platform
        if _platform.system() != "Darwin" and not args.no_cuda:
            if args.cuda or has_nvidia_gpu():
                extra_index = "https://download.pytorch.org/whl/cu121"
                print("NVIDIA GPUを検出: CUDA版PyTorchでLaMa環境を構築します")
        create_environment(ROOT / ".venv-iopaint", ROOT / "requirements-iopaint.txt",
                           extra_index_url=extra_index)

    if args.with_fonts:
        subprocess.run([str(python), str(ROOT / "fetch_fonts.py")], check=True)

    print("セットアップ完了。実行例:")
    print('  .venv/bin/python convert.py "input.pdf" -o output.pptx')


if __name__ == "__main__":
    main()
