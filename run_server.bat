@echo off
setlocal EnableDelayedExpansion
title RT-Subs (JA -> VI Real-Time Subtitles Server)
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo ============================================================================
echo   RT-Subs: Real-Time Japanese -^> Vietnamese Subtitle Server (Windows 10)
echo ============================================================================
echo.

:: 1. Auto-create .env from .env.example if missing
if not exist ".env" (
    if exist ".env.example" (
        echo [Setup] Creating .env from .env.example...
        copy /y ".env.example" ".env" >nul
        echo [Setup] Please paste your OPENAI_API_KEY in the Notepad window that opens, save, and close it.
        notepad ".env"
    )
)

:: 2. Check FFmpeg in PATH (auto-offer winget install if missing)
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo [Setup] FFmpeg not found in PATH. Attempting automatic install via winget...
    winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
)

:: 3. Locate Python (Custom env -> Conda rtsubs -> Local .venv -> System Python)
set "PY_EXE="

if defined PYTHON (
    if exist "%PYTHON%" set "PY_EXE=%PYTHON%"
)

if not defined PY_EXE (
    if exist "%USERPROFILE%\miniconda3\envs\rtsubs\python.exe" (
        set "PY_EXE=%USERPROFILE%\miniconda3\envs\rtsubs\python.exe"
    ) else if exist "%USERPROFILE%\anaconda3\envs\rtsubs\python.exe" (
        set "PY_EXE=%USERPROFILE%\anaconda3\envs\rtsubs\python.exe"
    ) else if exist ".venv\Scripts\python.exe" (
        set "PY_EXE=%~dp0.venv\Scripts\python.exe"
    )
)

:: 4. If no Conda or .venv exists yet, auto-create .venv using system python
if not defined PY_EXE (
    echo [Setup] First-time run detected! Creating local Python virtual environment (.venv)...
    python -m venv .venv
    if errorlevel 1 (
        echo [Error] Python 3.10+ was not found on your system.
        echo Please install Python 3.11 or Miniconda and run this script again.
        pause
        exit /b 1
    )
    set "PY_EXE=%~dp0.venv\Scripts\python.exe"
)

echo [Info] Using Python: "%PY_EXE%"

:: 5. Auto-install dependencies on first run if faster_whisper or fastapi is not installed yet
"%PY_EXE%" -c "import fastapi, faster_whisper, silero_vad, openai, yt_dlp" >nul 2>nul
if errorlevel 1 (
    echo [Setup] Installing required packages automatically (first run only)...
    "%PY_EXE%" -m pip install --upgrade pip
    "%PY_EXE%" -c "import torch" >nul 2>nul
    if errorlevel 1 (
        echo [Setup] Installing PyTorch with CUDA 12.4 support...
        "%PY_EXE%" -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
    )
    "%PY_EXE%" -m pip install -r requirements.txt
)

:: 6. Launch the server
echo.
echo [Server] Starting RT-Subs Subtitle Server on http://127.0.0.1:8765 ...
cd /d "%~dp0server"
"%PY_EXE%" -m rtsubs.server

if errorlevel 1 (
    echo.
    echo [Error] Server stopped with an error.
    pause
)
