@echo off
:: Starts M.B.S Studio in the browser. The old PyQt desktop GUI: python -m src.gui.app
:: (needs: pip install -r requirements-desktop.txt)

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found in .venv
    pause
    exit /b 1
)

echo Starting M.B.S Studio in your browser...
call .venv\Scripts\python.exe -m src.web --stay

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [CRASH] Application closed with error code %ERRORLEVEL%
    pause
)
