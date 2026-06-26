@echo off
REM ===========================================================
REM Windows 用スタンドアロン実行ファイル (.exe) をビルドする。
REM Python (python.org 版) がインストールされた Windows で実行。
REM 出力: dist\pptx-vectorizer.exe
REM ===========================================================
cd /d "%~dp0"

echo [1/3] 依存ライブラリをインストールしています...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :error

echo [2/3] 実行ファイルをビルドしています...
python -m PyInstaller --noconfirm pptx_vectorizer.spec
if errorlevel 1 goto :error

echo [3/3] 完了！
echo 実行ファイル: dist\pptx-vectorizer.exe
echo このファイルをダブルクリックするとアプリが起動します。
pause
exit /b 0

:error
echo.
echo ビルドに失敗しました。上のエラーメッセージを確認してください。
pause
exit /b 1
