@echo off
REM Windows 用ランチャー。ダブルクリックで起動。
cd /d "%~dp0"
python run_gui.py
if errorlevel 1 pause
