@echo off
setlocal

echo ============================================
echo  Stopping Backend and Frontend
echo ============================================
echo.

:: Close the windows started by start.bat (kills child Python / Node with /T)
taskkill /FI "WINDOWTITLE eq Backend - FastAPI*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Frontend - Vite*" /T /F >nul 2>&1

:: Fallback: stop anything still listening on the app ports (manual runs, title mismatch)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "foreach ($port in 5001, 5173) { Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } }" 2>nul

echo [OK] Stopped services on ports 5001 and 5173 ^(if they were running^).
echo      Close tunnel.bat manually if you used a public URL.
echo.
pause
