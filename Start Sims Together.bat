@echo off
cd /d "%~dp0"
py -3 launcher.py
if errorlevel 1 (
  echo.
  echo Could not start with Windows Python launcher; trying python on PATH...
  python launcher.py
)
pause
