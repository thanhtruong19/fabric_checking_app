@echo off
setlocal
cd /d "%~dp0"

echo [DEV MODE] Dang khoi chay VEO3 Auto Pipeline...

if exist "%~dp0dist\VEO3_AUTO_APP_DEV.exe" (
    echo Khoi chay qua dev launcher: %~dp0dist\VEO3_AUTO_APP_DEV.exe
    start "" "%~dp0dist\VEO3_AUTO_APP_DEV.exe"
    exit /b 0
)

if exist "%~dp0dist\dev\VEO3_AUTO_APP_DEV\VEO3_AUTO_APP_DEV.exe" (
    echo Khoi chay ban PyInstaller dev: %~dp0dist\dev\VEO3_AUTO_APP_DEV\VEO3_AUTO_APP_DEV.exe
    start "" "%~dp0dist\dev\VEO3_AUTO_APP_DEV\VEO3_AUTO_APP_DEV.exe"
    exit /b 0
)

set "PYTHON_EXE="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%~fD\python.exe" set "PYTHON_EXE=%%~fD\python.exe"
if not defined PYTHON_EXE for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%I"

if defined PYTHON_EXE (
    echo Khoi chay truc tiep voi Python: %PYTHON_EXE%
    start "VEO3 AUTO APP - [DEV MODE]" "%PYTHON_EXE%" "%~dp0veo3_auto_app.py"
    exit /b 0
)

echo ERROR: Khong tim thay Python hoac file dev de khoi chay.
pause
exit /b 1
