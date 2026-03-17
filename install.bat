@echo off
setlocal enabledelayedexpansion

echo ============================================
echo  Installing ALL Dependencies
echo ============================================
echo.

:: Check for npm / Node.js - auto-install if missing
where npm >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [INFO] Node.js / npm not found. Downloading and installing Node.js LTS...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$lts = (Invoke-RestMethod 'https://nodejs.org/dist/index.json') | Where-Object { $_.lts } | Select-Object -First 1; " ^
        "$ver = $lts.version; " ^
        "$url = \"https://nodejs.org/dist/$ver/node-$ver-x64.msi\"; " ^
        "Write-Host \"Downloading Node.js $ver from $url\"; " ^
        "Invoke-WebRequest $url -OutFile \"$env:TEMP\nodejs_lts.msi\"; " ^
        "Write-Host 'Installing Node.js (this may take a minute)...'; " ^
        "Start-Process msiexec.exe -ArgumentList \"/i $env:TEMP\nodejs_lts.msi /quiet /norestart ADDLOCAL=ALL\" -Wait; " ^
        "Write-Host 'Node.js installed.'"
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to download or install Node.js automatically.
        echo Please install manually from https://nodejs.org/ and re-run this script.
        pause
        exit /b 1
    )
    echo [OK] Node.js installed.
    echo [INFO] Re-launching installer in a fresh shell to pick up new PATH...
    echo.
    :: Re-launch this script in a new cmd session so Node.js PATH is active
    start "install" cmd /k ""%~f0" && exit"
    exit /b 0
)

:: Check for pnpm - auto-install if missing
where pnpm >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [INFO] pnpm not found. Installing via npm...
    npm install -g pnpm
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to install pnpm via npm.
        pause
        exit /b 1
    )
    echo [OK] pnpm installed successfully.
    echo.
)

:: Check for Python
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    pause
    exit /b 1
)

:: Check for pip
where pip >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] pip is not installed or not in PATH.
    pause
    exit /b 1
)

echo [1/2] Installing frontend dependencies (pnpm workspaces)...
echo -----------------------------------------------
pnpm install
if %ERRORLEVEL% neq 0 (
    echo [ERROR] pnpm install failed.
    pause
    exit /b 1
)
echo [OK] Frontend dependencies installed.
echo.

echo [2/2] Installing backend dependencies (Python / pyproject.toml)...
echo -----------------------------------------------
pip install .
if %ERRORLEVEL% neq 0 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)
echo [OK] Backend dependencies installed.
echo.

echo ============================================
echo  All dependencies installed successfully!
echo ============================================
pause
