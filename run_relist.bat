@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 relist.py
) else (
    python relist.py
)
if errorlevel 1 (
    echo.
    echo The program exited with an error.
    pause
)
