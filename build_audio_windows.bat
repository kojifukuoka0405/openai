@echo off
REM ===========================================================
REM 音声文字起こしアプリの Windows 用実行ファイル (.exe) をビルドする。
REM Python (python.org 版) がインストールされた Windows で実行。
REM 出力: dist\audio-transcriber.exe
REM ===========================================================
cd /d "%~dp0"

echo [1/3] 依存ライブラリをインストールしています...
python -m pip install --upgrade pip
python -m pip install openai pyinstaller
if errorlevel 1 goto :error

echo [2/3] 実行ファイルをビルドしています...
python -m PyInstaller --noconfirm audio_transcriber.spec
if errorlevel 1 goto :error

echo [3/3] 完了！
echo 実行ファイル: dist\audio-transcriber.exe
echo このファイルをダブルクリックするとアプリが起動します。
pause
exit /b 0

:error
echo.
echo ビルドに失敗しました。上のエラーメッセージを確認してください。
pause
exit /b 1
