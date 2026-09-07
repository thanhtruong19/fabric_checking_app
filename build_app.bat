@echo off
setlocal

set "PYTHON_EXE="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%~fD\python.exe" set "PYTHON_EXE=%%~fD\python.exe"
if not defined PYTHON_EXE for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%I"
if not defined PYTHON_EXE if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "PYTHON_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if not defined PYTHON_EXE (
    echo ERROR: Python was not found. Install Python 3 first.
    pause
    exit /b 1
)

cd /d "%~dp0"
echo Using Python: %PYTHON_EXE%
echo Installing/updating standalone build dependencies...
"%PYTHON_EXE%" -m pip install --upgrade pyinstaller playwright requests pillow gdown numpy scipy
if errorlevel 1 goto :error

echo Preparing safe first-run assets...
"%PYTHON_EXE%" "%~dp0prepare_build_assets.py" "%~dp0config.json" "%~dp0build\runtime_assets\config.default.json"
if errorlevel 1 goto :error

echo Building VEO3_AUTO_APP.exe...
"%PYTHON_EXE%" "%~dp0build_with_pyinstaller.py" ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name VEO3_AUTO_APP ^
  --distpath "%~dp0dist" ^
  --workpath "%~dp0build\pyinstaller" ^
  --specpath "%~dp0build" ^
  --add-data "%~dp0build\runtime_assets\config.default.json;runtime_assets" ^
  --add-data "%~dp0prompts;runtime_assets\prompts" ^
  "%~dp0veo3_auto_app.py"
if errorlevel 1 goto :error

echo.
echo Build complete: %~dp0dist\VEO3_AUTO_APP.exe
echo This EXE includes Python and all pipeline modules. Google Chrome is still required.
pause
exit /b 0

:error
echo.
echo ERROR: App build failed.
pause
exit /b 1
