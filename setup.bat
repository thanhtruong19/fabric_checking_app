@echo off
setlocal

set "PYTHON_EXE="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%~fD\python.exe" set "PYTHON_EXE=%%~fD\python.exe"
if not defined PYTHON_EXE for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%I"

if not defined PYTHON_EXE (
    echo ERROR: Python was not found.
    echo Install Python 3 from https://www.python.org/downloads/ and run this file again.
    pause
    exit /b 1
)

echo Using Python: %PYTHON_EXE%
echo Installing required packages...
"%PYTHON_EXE%" -m pip install --upgrade playwright requests pillow "gdown>=6.2.0"
if errorlevel 1 (
    echo ERROR: Package installation failed.
    pause
    exit /b 1
)

echo Setup complete!
pause
