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
:: Load .env file if it exists
:: ══════════════════════════════════════════════
if exist "%ROOT%\.env" (
    echo [INFO] Loading .env file...
    for /f "usebackq tokens=1,* delims==" %%A in ("%ROOT%\.env") do (
        set "line=%%A"
        if not "!line:~0,1!"=="#" if not "%%A"=="" (
            set "%%A=%%B"
        )
    )
    echo [OK] Environment variables loaded.
    echo.
)

:: ══════════════════════════════════════════════
:: Launch each service in its own cmd window
:: ══════════════════════════════════════════════

echo [1/2] Starting Backend - FastAPI (port 5001)...
start "Backend - FastAPI" cmd /k "cd /d %ROOT% && python -m uvicorn backend.main:app --host 127.0.0.1 --port 5001 --reload --timeout-keep-alive 300 --h11-max-incomplete-event-size 0 || (echo. & echo [ERROR] Backend failed - see above & pause)"

timeout /t 3 /nobreak >nul

echo [2/2] Starting Frontend - Vite doc-generator...
start "Frontend - Vite" cmd /k "cd /d %ROOT%\artifacts\doc-generator && pnpm dev || (echo. & echo [ERROR] Frontend failed - see above & pause)"

echo.
echo ============================================
echo  All services launched in separate windows.
echo  Backend  : http://localhost:5001
echo  Frontend : http://localhost:5173
echo ============================================
echo.
echo You can close this window.
pause
