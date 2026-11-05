@echo off
cd /d "%~dp0"
py -3 launcher.py 2>nul || python launcher.py
pause
