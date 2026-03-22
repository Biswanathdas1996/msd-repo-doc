@echo off
setlocal

echo ============================================
echo  Cloudflare Tunnel -^> local Vite (quick URL)
echo ============================================
echo.
echo  1. Run start.bat first (backend + frontend).
echo  2. Wait until http://localhost:5173 loads locally.
echo  3. Keep this window open; share the trycloudflare.com URL.
echo.

where cloudflared >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] cloudflared is not installed or not in PATH.
    echo Install with: winget install Cloudflare.cloudflared
    echo Then open a new Command Prompt and run tunnel.bat again.
    pause
    exit /b 1
)

if "%PORT%"=="" set "PORT=5173"

echo [INFO] Tunneling to http://localhost:%PORT% ...
echo.

cloudflared tunnel --url http://localhost:%PORT%
echo.
pause
