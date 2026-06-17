@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYVER=3.13.5"
set "PYTAG=313"
set "RT=runtime"
echo ============================================================
echo    se-autokey - Build bundled Python runtime  (Python %PYVER%)
echo    (run once on a machine WITH internet; then copy folder)
echo ============================================================
echo.

echo [1/6] Removing old runtime ...
if exist "%RT%" rmdir /s /q "%RT%"
mkdir "%RT%"

echo [2/6] Downloading Python %PYVER% embeddable ...
curl -L --fail -o "%RT%\python-embed.zip" "https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip"
if errorlevel 1 goto FAIL

echo [3/6] Extracting ...
tar -xf "%RT%\python-embed.zip" -C "%RT%"
if errorlevel 1 goto FAIL
del "%RT%\python-embed.zip"

echo [4/6] Configuring import paths (._pth) ...
> "%RT%\python%PYTAG%._pth" echo python%PYTAG%.zip
>>"%RT%\python%PYTAG%._pth" echo .
>>"%RT%\python%PYTAG%._pth" echo ..
>>"%RT%\python%PYTAG%._pth" echo Lib\site-packages
>>"%RT%\python%PYTAG%._pth" echo import site

echo [5/6] Installing pip ...
curl -L --fail -o "%RT%\get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
if errorlevel 1 goto FAIL
"%RT%\python.exe" "%RT%\get-pip.py" --no-warn-script-location
if errorlevel 1 goto FAIL
del "%RT%\get-pip.py"

echo [6/6] Installing packages from requirements.txt ...
"%RT%\python.exe" -m pip install --no-warn-script-location -r requirements.txt
if errorlevel 1 goto FAIL

echo.
echo Verifying imports ...
"%RT%\python.exe" -c "import selenium, cv2, numpy, PIL, rapidfuzz, requests; print('  all imports OK')"
if errorlevel 1 goto FAIL

echo.
echo ============================================================
echo    Runtime built OK.  The folder is now copy-and-run.
echo    Start the app with:  start-webui.bat
echo ============================================================
pause
exit /b 0

:FAIL
echo.
echo [ERROR] Build failed. Check the internet connection and retry.
pause
exit /b 1
