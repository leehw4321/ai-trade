@echo off
cd /d "%~dp0"
if exist stop.txt del stop.txt
if exist stop_shadow.txt del stop_shadow.txt
start "" pythonw bot.py
start "" pythonw shadow.py
if exist stop_exit_study.txt del stop_exit_study.txt
start "" pythonw exit_study.py
start "" pythonw pnl.py --loop 300
timeout /t 5 >nul
if exist data\status.html (
  if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
    start "" "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" "%~dp0data\status.html"
  ) else if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
    start "" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" "%~dp0data\status.html"
  ) else (
    start "" data\status.html
  )
)
