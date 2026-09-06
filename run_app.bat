@echo off
setlocal

set "PYTHON_EXE="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%~fD\python.exe" set "PYTHON_EXE=%%~fD\python.exe"
if not defined PYTHON_EXE for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%I"

if not defined PYTHON_EXE (
    echo ERROR: Python was not found. Run setup.bat after installing Python 3.
    pause
    exit /b 1
)

cd /d "%~dp0"
start "" "%PYTHON_EXE%" "%~dp0veo3_auto_app.py"
endlocal
