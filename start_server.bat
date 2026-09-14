@echo off
rem 傭工語言橋 - 本地啟動腳本
cd /d "%~dp0"
set "PATH=%~dp0.venv\Scripts;%~dp0..\tools\ffmpeg-9.0.1-essentials_build\bin;%PATH%"
set "WHISPER_MODEL=%~dp0models\whisper-small"
echo 啟動中... 瀏覽器打開 http://localhost:8000
"%~dp0.venv\Scripts\python.exe" app.py
