@echo off
rem ASCII only -- Thai text lives in tools\update_here.py
rem make_usb.py copies this file to the root of the USB update folder.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PY="
if exist "%~dp0runtime\python.exe" set "PY=%~dp0runtime\python.exe"
if not defined PY if exist "%USERPROFILE%\Desktop\se-autokey\runtime\python.exe" set "PY=%USERPROFILE%\Desktop\se-autokey\runtime\python.exe"
if not defined PY set "PY=python"
if exist "%~dp0tools\update_here.py" goto :run
echo.
echo   [ERROR] tools\update_here.py not found next to this file.
echo   Run make-usb.bat again to rebuild the USB folder.
echo.
pause
exit /b 1
:run
"%PY%" "%~dp0tools\update_here.py" %*
pause
exit /b 0
