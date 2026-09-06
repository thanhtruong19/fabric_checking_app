@echo off
setlocal

set "CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_PATH%" set "CHROME_PATH=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
set "CHROME_PROFILE=%LOCALAPPDATA%\VEO3_AUTO\ChromeProfile"
set "CDP_PORT=9333"

if not exist "%CHROME_PATH%" (
    echo ERROR: Google Chrome was not found.
    pause
    exit /b 1
)

if not exist "%CHROME_PROFILE%" mkdir "%CHROME_PROFILE%"

echo Starting the isolated VEO3 automation Chrome on port %CDP_PORT%...
echo Your normal Chrome windows will not be closed.
echo.
echo On the first run, sign in to Google for Flow or open ChatGPT and sign in.
echo Leave this automation window open while running any batch script.
echo.

start "" "%CHROME_PATH%" --remote-debugging-port=%CDP_PORT% --user-data-dir="%CHROME_PROFILE%" --no-first-run --no-default-browser-check "https://labs.google/fx/vi/tools/flow"
pause
