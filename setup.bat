@echo off
rem ============================================================
rem  notebooklm-pdf2pptx セットアップ (Windows)
rem  ダブルクリックで実行。Pythonが無ければ自動インストールします。
rem ============================================================
setlocal
cd /d "%~dp0"
chcp 65001 >nul

where py >nul 2>nul
if %errorlevel%==0 goto :have_python
where python >nul 2>nul
if %errorlevel%==0 goto :have_python

echo Python が見つかりません。インストールします (Windows標準のwingetを使用)...
winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
if %errorlevel% neq 0 (
  echo.
  echo 自動インストールに失敗しました。https://www.python.org/downloads/ から
  echo Python 3.12 をインストールして、このファイルをもう一度実行してください。
  pause
  exit /b 1
)
echo.
echo Python をインストールしました。
echo このウィンドウを閉じて、setup.bat を「もう一度」実行してください。
echo (インストール直後は新しいウィンドウでないとPythonが見えないため)
pause
exit /b 0

:have_python
echo セットアップを開始します (数分かかります)...
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 bootstrap.py --with-lama --with-fonts
) else (
  python bootstrap.py --with-lama --with-fonts
)
if %errorlevel% neq 0 (
  echo セットアップに失敗しました。上のメッセージを確認してください。
  pause
  exit /b 1
)
echo.
echo セットアップ完了。回答の自動取り込みを常駐させる場合は次を実行:
echo   .venv\Scripts\python tools\install_watcher.py
pause
