@echo off
:: Re-launch inside a persistent cmd.exe /k window if double-clicked so the window NEVER closes on its own
if /i not "%~1"=="__keep_open__" (
    cmd /k ""%~f0" __keep_open__"
    exit /b
)

title RT-Subs (JA -^> VI Real-Time Subtitles Server)
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

:: 2. Check FFmpeg in PATH or WinGet Links folder
where ffmpeg >nul 2>nul
if errorlevel 1 (
    if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" (
        set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
    )
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo [Setup] FFmpeg not found in PATH. Installing via winget...
    winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" (
        set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
    )
)

:: 3. Locate Python (avoiding the broken Windows Store Microsoft\WindowsApps\python.exe alias)
set "PY_EXE="

if defined PYTHON (
    if exist "%PYTHON%" set "PY_EXE=%PYTHON%"
)

if not defined PY_EXE (
    if exist "%USERPROFILE%\miniconda3\envs\rtsubs\python.exe" set "PY_EXE=%USERPROFILE%\miniconda3\envs\rtsubs\python.exe"
)
if not defined PY_EXE (
    if exist "%USERPROFILE%\anaconda3\envs\rtsubs\python.exe" set "PY_EXE=%USERPROFILE%\anaconda3\envs\rtsubs\python.exe"
)
if not defined PY_EXE (
    if exist "%~dp0.venv\Scripts\python.exe" set "PY_EXE=%~dp0.venv\Scripts\python.exe"
)
if not defined PY_EXE (
    if exist "%USERPROFILE%\miniconda3\python.exe" set "PY_EXE=%USERPROFILE%\miniconda3\python.exe"
)
if not defined PY_EXE (
    if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
)
if not defined PY_EXE (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PY_EXE (
    if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
)

:: 4. If still not found, test `py -3` or `python` (ensuring it is real Python, not Windows Store stub)
set "SYS_PY="
if not defined PY_EXE (
    py -3 -c "import sys; print(sys.version)" >nul 2>nul
    if not errorlevel 1 set "SYS_PY=py -3"
)
if not defined PY_EXE if not defined SYS_PY (
    python -c "import sys; print(sys.version)" >nul 2>nul
    if not errorlevel 1 set "SYS_PY=python"
)

:: 5. If no real Python is installed on Windows 10 at all, install Python 3.11 via winget
if not defined PY_EXE if not defined SYS_PY (
    echo [Setup] Python 3.11 was not found on your PC. Installing Python 3.11 via winget...
    winget install Python.Python.3.11 --accept-package-agreements --accept-source-agreements
    if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
        set "SYS_PY="%LOCALAPPDATA%\Programs\Python\Python311\python.exe""
    ) else (
        echo.
        echo [Error] Could not locate Python after installation.
        echo Please close this window and double-click run_server.bat again, or install Python 3.11 from python.org.
        pause
        exit /b 1
    )
)

:: 6. Create .venv if we only found system Python
if not defined PY_EXE (
    echo [Setup] Creating local virtual environment (.venv)...
    %SYS_PY% -m venv "%~dp0.venv"
    if not exist "%~dp0.venv\Scripts\python.exe" (
        echo [Error] Failed to create .venv using %SYS_PY%.
        pause
        exit /b 1
    )
    set "PY_EXE=%~dp0.venv\Scripts\python.exe"
)

echo [Info] Using Python: %PY_EXE%

:: 7. Auto-install dependencies on first run if faster_whisper or fastapi is missing
"%PY_EXE%" -c "import fastapi, faster_whisper, silero_vad, openai, yt_dlp" >nul 2>nul
if errorlevel 1 (
    echo.
    echo [Setup] Installing required AI packages into environment (first run only, please wait)...
    "%PY_EXE%" -m pip install --upgrade pip
    "%PY_EXE%" -c "import torch" >nul 2>nul
    if errorlevel 1 (
        echo [Setup] Installing PyTorch with CUDA 12.4 support...
        "%PY_EXE%" -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
    )
    "%PY_EXE%" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo.
        echo [Error] Package installation failed! See error messages above.
        pause
        exit /b 1
    )
)

:: 8. Launch the server
echo.
echo [Server] Starting RT-Subs Subtitle Server on http://127.0.0.1:8765 ...
cd /d "%~dp0server"
"%PY_EXE%" -m rtsubs.server

echo.
echo [Server] Process exited with code %errorlevel%.
pause
