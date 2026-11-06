@echo off
chcp 65001 >nul
cd /d "%~dp0"
python tools\handoff_sync.py pull
pause
