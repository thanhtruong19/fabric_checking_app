@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%~fD\python.exe" set "PYTHON_EXE=%%~fD\python.exe"
if not defined PYTHON_EXE for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%I"
if not defined PYTHON_EXE if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "PYTHON_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if defined PYTHON_EXE (
    echo [DEV MODE] Backend restart + frontend live reload
    "%PYTHON_EXE%" "%~dp0dev_server.py"
    exit /b %ERRORLEVEL%
)

echo ERROR: Khong tim thay Python.
pause
exit /b 1
