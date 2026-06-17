@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo    se-autokey - Setup (install dependencies)
echo ============================================================
echo.
python --version >nul 2>&1
if errorlevel 1 goto NOPYTHON
python --version
echo.
echo Installing required packages (selenium, opencv, requests, ...)
python -m pip install -r requirements.txt
if errorlevel 1 goto FAILED
echo.
echo ============================================================
echo    Setup done!  Double-click  start-webui.bat  to run.
echo ============================================================
pause
exit /b 0
:NOPYTHON
echo [ERROR] Python not found.
echo Install Python 3.10+ from https://www.python.org/downloads/
echo and TICK "Add Python to PATH", then run setup.bat again.
pause
exit /b 1
:FAILED
echo [ERROR] pip install failed. Check internet connection and try again.
pause
exit /b 1
