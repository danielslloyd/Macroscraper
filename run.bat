@echo off
setlocal enabledelayedexpansion

REM ----- Python venv + deps ------------------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python not found. Install Python 3.11+ and add to PATH.
    exit /b 1
)

if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

call venv\Scripts\activate.bat

echo Installing Python dependencies...
pip install -q -U pip setuptools wheel
pip install -q -r requirements.txt

echo Installing Playwright browsers (chromium)...
playwright install chromium

REM ----- UI deps -----------------------------------------------------------
if not exist "ui\node_modules" (
    echo Installing UI dependencies...
    pushd ui
    npm install
    popd
)

REM ----- Launch ------------------------------------------------------------
echo Starting FastAPI server on http://localhost:8000 ...
start "food-scraper-api" cmd /k "call venv\Scripts\activate.bat && python -m orchestrator.main"
timeout /t 2 >nul

echo Starting React dev server on http://localhost:5173 ...
start "food-scraper-ui" cmd /k "cd ui && npm run dev"
timeout /t 3 >nul

start http://localhost:5173

echo.
echo ====================================
echo FastAPI: http://localhost:8000/api/health
echo UI:      http://localhost:5173
echo ====================================
echo.
pause
