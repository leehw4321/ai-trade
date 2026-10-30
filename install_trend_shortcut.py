"""Create the desktop shortcut using built-in Windows components. No policy changes."""
import math
import os
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def line_distance(x, y, ax, ay, bx, by):
    t = max(0, min(1, ((x-ax)*(bx-ax)+(y-ay)*(by-ay))/((bx-ax)**2+(by-ay)**2)))
    return math.hypot(x-ax-t*(bx-ax), y-ay-t*(by-ay))


def icon():
    pixels = bytearray()
    segments = [(10,44,25,29), (25,29,35,36), (35,36,53,16), (40,16,53,16), (53,16,53,29)]
    for y in reversed(range(64)):
        for x in range(64):
            if 10<=x<=53 and 52<=y<=56:
                r,g,b = (255,178,102)
            elif any(line_distance(x,y,*s)<=2.5 for s in segments):
                r,g,b = (56,219,176)
            else:
                r,g,b = (12,19,35)
            pixels.extend((b,g,r,255))
    dib = struct.pack('<IiiHHIIiiII',40,64,128,1,32,0,len(pixels),0,0,0,0)+pixels+bytes(64*8)
    header = struct.pack('<HHH',0,1,1)+struct.pack('<BBBBHHII',64,64,0,0,1,32,len(dib),22)
    (ROOT/'trend_bot.ico').write_bytes(header+dib)


if __name__ == '__main__':
    icon()
    env = os.environ.copy()
    env['TREND_SHORTCUT_ROOT'] = str(ROOT)
    env['TREND_SHORTCUT_PYTHON'] = str(Path(sys.executable).with_name('pythonw.exe'))
    command = '''
$taskRoot = $env:TREND_SHORTCUT_ROOT
$shortcutPath = Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) '자동거래 v4 실행.lnk'
$wshell = New-Object -ComObject WScript.Shell
$shortcut = $wshell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $env:TREND_SHORTCUT_PYTHON
$shortcut.Arguments = '"' + (Join-Path $taskRoot 'launch_trend_web.py') + '"'
$shortcut.WorkingDirectory = $taskRoot
$shortcut.IconLocation = (Join-Path $taskRoot 'trend_bot.ico') + ',0'
$shortcut.Description = '자동거래 v4 · 비용·변동성 필터 · 모의거래'
$shortcut.Save()
Write-Output $shortcutPath
'''
    subprocess.run(['powershell.exe','-NoProfile','-Command',command],env=env,check=True)
