@echo off
rem ASCII only -- Thai text lives in tools\make_release.py
rem 1) build dist\se-autokey-<version>.zip + dist\latest.json (code only, same rules as make-usb)
rem 2) publish to R2 through se-survey backend (dev machine only: ..\se-survey must exist)
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0tools\make_release.py" %*
if errorlevel 1 goto FAIL
if not exist "%~dp0..\se-survey\backend\package.json" (
  echo.
  echo   [i] ..\se-survey not found - upload dist\ manually with backend script publishBotRelease.ts
  goto END
)
echo.
echo   publishing to R2 via se-survey backend ...
pushd "%~dp0..\se-survey\backend"
call npx ts-node --transpile-only src\scripts\publishBotRelease.ts "%~dp0dist"
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" goto FAIL
goto END
:FAIL
echo.
echo   [ERROR] release failed (see messages above)
pause
exit /b 1
:END
pause
exit /b 0
