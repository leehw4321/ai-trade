@echo off
cd /d "%~dp0"
if exist stop.txt del stop.txt
if exist stop_shadow.txt del stop_shadow.txt
start "" pythonw bot.py
start "" pythonw shadow.py
timeout /t 5 >nul
if exist data\status.html start "" data\status.html
