#!/bin/sh
# ============================================================
#  notebooklm-pdf2pptx セットアップ (macOS / Linux)
#  実行: sh setup.sh
#  Python3が無ければ導入方法を案内(可能なら自動導入)します。
# ============================================================
set -e
cd "$(dirname "$0")"

find_python() {
  for c in python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; raise SystemExit(0 if (3,10) <= sys.version_info < (3,13) else 1)' \
          >/dev/null 2>&1; then
        echo "$c"; return 0
      fi
    fi
  done
  return 1
}

PY="$(find_python || true)"

if [ -z "$PY" ]; then
  echo "対応する Python (3.10〜3.12) が見つかりません。導入を試みます..."
  case "$(uname -s)" in
    Darwin)
      if command -v brew >/dev/null 2>&1; then
        brew install python@3.12
      else
        echo "macOSの開発者ツール(Python同梱)のインストール画面を開きます。"
        echo "完了後、もう一度 sh setup.sh を実行してください。"
        xcode-select --install || true
        exit 1
      fi ;;
    Linux)
      if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip
      elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y python3
      else
        echo "パッケージ管理からPython 3.10〜3.12を入れて再実行してください。"
        exit 1
      fi ;;
  esac
  PY="$(find_python)" || { echo "Pythonの導入に失敗しました。"; exit 1; }
fi

echo "Python: $PY を使ってセットアップします (数分かかります)..."
"$PY" bootstrap.py --with-lama --with-fonts
echo
echo "セットアップ完了。回答の自動取り込みを常駐させる場合は次を実行:"
echo "  .venv/bin/python tools/install_watcher.py"
