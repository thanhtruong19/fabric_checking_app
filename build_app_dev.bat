@echo off
setlocal

cd /d "%~dp0"

echo ============================================================
echo   [DEV BUILD] TAO FILE DEV CHO VEO3 AUTO PIPELINE
echo ============================================================
echo.

REM 1. Tim Python
set "PYTHON_EXE="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%~fD\python.exe" set "PYTHON_EXE=%%~fD\python.exe"
if not defined PYTHON_EXE for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%I"
if not defined PYTHON_EXE if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "PYTHON_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if not defined PYTHON_EXE (
    echo ERROR: Python 3 khong tim thay tren may.
    pause
    exit /b 1
)

echo Using Python: %PYTHON_EXE%

REM 2. Tim CSC (C# Compiler tich hop san tren Windows)
set "CSC_EXE="
if exist "%SystemRoot%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" set "CSC_EXE=%SystemRoot%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not defined CSC_EXE if exist "%SystemRoot%\Microsoft.NET\Framework\v4.0.30319\csc.exe" set "CSC_EXE=%SystemRoot%\Microsoft.NET\Framework\v4.0.30319\csc.exe"

if not exist "%~dp0dist" mkdir "%~dp0dist"

echo.
echo [1/2] Dang tao file Dev Launcher: dist\VEO3_AUTO_APP_DEV.exe...
taskkill /f /im VEO3_AUTO_APP_DEV.exe >nul 2>&1

if not defined CSC_EXE goto :skip_csc
"%CSC_EXE%" /nologo /target:exe /out:"%~dp0dist\VEO3_AUTO_APP_DEV.exe" "%~dp0build\dev_launcher.cs"
if errorlevel 1 goto :skip_csc

echo [THANH CONG] Da tao file: %~dp0dist\VEO3_AUTO_APP_DEV.exe
echo   * Chay truc tiep code Python moi nhat, KHONG CAN BUILD LAI moi khi sua code.
echo   * Thoi gian chay: 0 giay build!
echo   * Co Console Debug truc tiep de xem log va bat loi realtime.
goto :prompt_pyinstaller

:skip_csc
echo [CANH BAO] Khong tim thay csc.exe, chuyen sang che do PyInstaller...

:prompt_pyinstaller
echo.
echo ============================================================
echo Ban co muon dong goi them ban PyInstaller Dev [Onedir] khong?
echo [Chi can thiet neu muon test dong goi PyInstaller tach roi]
set /p BUILD_PYINSTALLER="Build PyInstaller Dev? [y/N]: "
if /i not "%BUILD_PYINSTALLER%"=="y" goto :done

echo.
echo [2/2] Dang build ban PyInstaller Dev [Onedir + Console, tan dung cache]...
"%PYTHON_EXE%" "%~dp0tools\build\prepare_build_assets.py" "%~dp0config.json" "%~dp0build\runtime_assets\config.default.json"
if errorlevel 1 goto :error

"%PYTHON_EXE%" "%~dp0tools\build\build_with_pyinstaller.py" ^
  --noconfirm ^
  --onedir ^
  --console ^
  --name VEO3_AUTO_APP_DEV ^
  --distpath "%~dp0dist\dev" ^
  --workpath "%~dp0build\pyinstaller_dev" ^
  --specpath "%~dp0build" ^
  --add-data "%~dp0build\runtime_assets\config.default.json;runtime_assets" ^
  --add-data "%~dp0prompts;runtime_assets\prompts" ^
  --add-data "%~dp0dashboard;runtime_assets\dashboard" ^
  --add-data "%~dp0tools\windows\windows_folder_picker.ps1;runtime_assets" ^
  "%~dp0veo3_auto_app.py"
if errorlevel 1 goto :error

echo.
echo [THANH CONG] PyInstaller Dev build hoan tat:
echo   %~dp0dist\dev\VEO3_AUTO_APP_DEV\VEO3_AUTO_APP_DEV.exe

:done
echo.
echo ============================================================
echo HOAN TAT!
echo   * File DEV moi lan sua code:  dist\VEO3_AUTO_APP_DEV.exe
echo   * File FINAL dong goi day du: dist\VEO3_AUTO_APP.exe
echo ============================================================
pause
exit /b 0

:error
echo.
echo ERROR: Build that bai.
pause
exit /b 1
