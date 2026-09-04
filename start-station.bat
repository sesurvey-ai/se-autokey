@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"
echo Starting se-autokey EMCS import STATION ...
echo (receives jobs from the se-survey web queue, logs in to EMCS once, runs until Ctrl+C)
echo.
"%PY%" -u main.py --station %*
if errorlevel 1 (
  echo.
  echo [ERROR] The station stopped with an error. See runs\logs for details.
  pause
)
exit /b 0
