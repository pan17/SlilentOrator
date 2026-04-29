@echo off
chcp 65001 >nul
echo ==========================================
echo    SlilentOrator - PPT Remote Controller
echo ==========================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found
    pause
    exit /b 1
)

REM Check virtual environment
if not exist venv (
    echo [INFO] Creating virtual environment...
    python -m venv venv
)

echo [INFO] Activating virtual environment...
call venv\Scripts\activate.bat

REM Install dependencies
echo [INFO] Checking dependencies...
pip install -q -r server\requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency installation failed
    pause
    exit /b 1
)

echo.
echo [INFO] Starting server...
echo Server: http://localhost:8000
echo WebUI:  http://localhost:8000
echo Docs:   http://localhost:8000/docs
echo.

python server\main.py

echo.
echo [INFO] Server stopped
pause