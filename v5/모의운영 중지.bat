@echo off
cd /d "%~dp0"
echo stop> stop.txt
echo stop> stop_shadow.txt
echo Bot and shadow test will stop within 1 minute.
timeout /t 5 >nul
