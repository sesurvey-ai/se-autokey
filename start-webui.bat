@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"
echo Starting se-autokey web UI ...
echo (a browser tab will open automatically)
echo.
"%PY%" webui.py
if errorlevel 1 (
  echo.
  echo [ERROR] Could not start the app.
  echo  - If you see "not recognized": the bundled runtime is missing
  echo    AND Python is not installed on this PC.
  echo    Ask the admin to run  build-runtime.bat  once on a PC with
  echo    internet, then copy the WHOLE folder ^(including runtime\^) again.
  echo.
  pause
)
exit /b 0
