@echo off
setlocal

set PROFILE=%1
if "%PROFILE%"=="" set PROFILE=auto

set PYMODE=%2
if "%PYMODE%"=="" set PYMODE=check-only

set VENV_DIR=%3
if "%VENV_DIR%"=="" set VENV_DIR=.venv

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 scripts\install_env.py --profile %PROFILE% --python-mode %PYMODE% --venv %VENV_DIR%
    exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel%==0 (
    python scripts\install_env.py --profile %PROFILE% --python-mode %PYMODE% --venv %VENV_DIR%
    exit /b %errorlevel%
)

echo Python launcher not found. Install Python 3.11 first.
exit /b 1
