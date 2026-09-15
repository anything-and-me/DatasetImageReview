@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 -m venv "%SCRIPT_DIR%.venv"
    ) else (
        python -m venv "%SCRIPT_DIR%.venv"
    )
    if errorlevel 1 exit /b %errorlevel%
)

"%VENV_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo Requires Python 3.10 or newer.
    exit /b 2
)
"%VENV_PYTHON%" -m pip install --disable-pip-version-check -r "%SCRIPT_DIR%requirements.txt"
if errorlevel 1 exit /b %errorlevel%
"%VENV_PYTHON%" "%SCRIPT_DIR%portable_launch.py" %*
