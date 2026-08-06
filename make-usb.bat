@echo off
rem ASCII only -- Thai text lives in tools\make_usb.py
rem (cmd.exe mis-parses batch files that contain multi-byte characters)
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0tools\make_usb.py" %*
pause
exit /b 0
