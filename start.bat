@echo off
setlocal enabledelayedexpansion

echo ============================================
echo  Starting Frontend and Backend Services
echo ============================================
echo.

:: Workspace root = directory this bat file lives in
set "ROOT=%~dp0"
:: Strip trailing backslash
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

:: Move to workspace root immediately
cd /d "%ROOT%"

:: ── Node.js check ─────────────────────────────
where npm >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Node.js is not installed. Please run install.bat first.
    pause & exit /b 1
)

:: ── pnpm check / install ──────────────────────
where pnpm >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [INFO] pnpm not found. Installing via npm...
    npm install -g pnpm
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to install pnpm.
        pause & exit /b 1
    )
    echo [OK] pnpm installed.
    echo.
)

:: ── Python check ──────────────────────────────
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    pause & exit /b 1
)



:: ══════════════════════════════════════════════
:: Launch each service in its own cmd window
:: ══════════════════════════════════════════════

echo [1/3] Starting Backend - FastAPI (port 5001)...
start "Backend - FastAPI" cmd /k "cd /d %ROOT% && python -m uvicorn backend.main:app --host 0.0.0.0 --port 5001 --reload || (echo. & echo [ERROR] Backend failed - see above & pause)"

timeout /t 3 /nobreak >nul

echo [2/3] Starting Frontend - Vite doc-generator...
start "Frontend - Vite" cmd /k "cd /d %ROOT%\artifacts\doc-generator && pnpm dev || (echo. & echo [ERROR] Frontend failed - see above & pause)"

echo [3/3] Starting API Server - Express...
start "API Server - Express" cmd /k "cd /d %ROOT%\artifacts\api-server && set NODE_ENV=development && pnpm exec tsx ./src/index.ts || (echo. & echo [ERROR] API Server failed - see above & pause)"

echo.
echo ============================================
echo  All services launched in separate windows.
echo  Backend  : http://localhost:5001
echo  Frontend : http://localhost:5173
echo  API Srv  : http://localhost:3000
echo ============================================
echo.
echo You can close this window.
pause
