@echo off
cd /d "%~dp0"
py -3.7 build_mod.py
if errorlevel 1 (
 echo.
 echo Python 3.7 is required for the Sims 4 mod build.
 echo Check installed Python versions using: py -0p
)
pause
