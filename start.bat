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

REM Check and install VLC
echo [INFO] Checking VLC...
set VLC_FOUND=0

where vlc >nul 2>&1
if not errorlevel 1 (
    set VLC_FOUND=1
    goto :vlc_ok
)

if exist "C:\Program Files\VideoLAN\VLC\vlc.exe" (
    set "PATH=C:\Program Files\VideoLAN\VLC;%PATH%"
    set VLC_FOUND=1
    goto :vlc_ok
)
if exist "%LOCALAPPDATA%\Programs\VideoLAN\VLC\vlc.exe" (
    set "PATH=%LOCALAPPDATA%\Programs\VideoLAN\VLC;%PATH%"
    set VLC_FOUND=1
    goto :vlc_ok
)

echo [INFO] VLC not found, attempting to install...

where winget >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Installing VLC via winget...
    winget install VideoLAN.VLC --accept-source-agreements --accept-package-agreements --silent
    if not errorlevel 1 (
        echo [INFO] VLC installed via winget
        set "PATH=C:\Program Files\VideoLAN\VLC;%PATH%"
        set VLC_FOUND=1
        goto :vlc_ok
    )
    echo [WARN] winget install failed, trying chocolatey...
)

where choco >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Installing VLC via chocolatey...
    choco install vlc -y --no-progress
    if not errorlevel 1 (
        echo [INFO] VLC installed via chocolatey
        set "PATH=C:\Program Files\VideoLAN\VLC;%PATH%"
        set VLC_FOUND=1
        goto :vlc_ok
    )
    echo [WARN] chocolatey install failed
)

echo.
echo [ERROR] VLC not found and auto-install failed.
echo Please download and install VLC manually:
echo   https://www.videolan.org/vlc/
echo.
pause
exit /b 1

:vlc_ok
echo [INFO] VLC found: OK

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
powershell -NoProfile -Command "$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch 'Loopback|Virtual|Bluetooth|VMware|Hyper-V' -and $_.PrefixOrigin -eq 'Dhcp' }).IPAddress; if ($ip) { Write-Host (' LAN: http://' + $ip + ':8000') }"
echo.

python server\main.py

echo.
echo [INFO] Server stopped
pause
