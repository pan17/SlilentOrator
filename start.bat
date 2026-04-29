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
pip install -q -r server\requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
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
REM 获取局域网IP提示
powershell -NoProfile -Command "$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch 'Loopback|Virtual|Bluetooth|VMware|Hyper-V' -and $_.PrefixOrigin -eq 'Dhcp' }).IPAddress; if ($ip) { Write-Host (' 手机/局域网: http://' + $ip + ':8000') }"
echo.

python server\main.py

echo.
echo [INFO] Server stopped
pause