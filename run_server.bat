@echo off
:: Re-launch inside a persistent cmd.exe /k window if double-clicked so the window NEVER closes on its own
if /i not "%~1"=="__keep_open__" cmd /k ""%~f0" __keep_open__" & exit /b

title RT-Subs JA to VI Real-Time Subtitles Server
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo ============================================================================
echo   RT-Subs: Real-Time Japanese to Vietnamese Subtitle Server - Windows 10
echo ============================================================================
echo.

:: 1. Auto-create .env from .env.example if missing
if exist ".env" goto :check_ffmpeg
if not exist ".env.example" goto :check_ffmpeg
echo [Setup] Creating .env from .env.example...
copy /y ".env.example" ".env" >nul
echo [Setup] Please paste your OPENROUTER_API_KEY in Notepad, save, and close it.
notepad ".env"

:check_ffmpeg
:: 2. Check FFmpeg in PATH or WinGet Links folder (Zero parenthesized blocks so (x86) in PATH never breaks cmd.exe)
where ffmpeg >nul 2>nul
if not errorlevel 1 goto :find_python
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
where ffmpeg >nul 2>nul
if not errorlevel 1 goto :find_python

echo [Setup] FFmpeg not found in PATH. Installing FFmpeg via winget...
winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe" set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"

:find_python
:: 3. Locate Python (avoiding broken Windows Store python.exe stub)
set "PY_EXE="

if defined PYTHON if exist "%PYTHON%" set "PY_EXE=%PYTHON%"
if defined PY_EXE goto :have_python

if exist "%USERPROFILE%\miniconda3\envs\rtsubs\python.exe" set "PY_EXE=%USERPROFILE%\miniconda3\envs\rtsubs\python.exe"
if defined PY_EXE goto :have_python

if exist "%USERPROFILE%\anaconda3\envs\rtsubs\python.exe" set "PY_EXE=%USERPROFILE%\anaconda3\envs\rtsubs\python.exe"
if defined PY_EXE goto :have_python

if exist "%~dp0.venv\Scripts\python.exe" set "PY_EXE=%~dp0.venv\Scripts\python.exe"
if defined PY_EXE goto :have_python

if exist "%USERPROFILE%\miniconda3\python.exe" set "PY_EXE=%USERPROFILE%\miniconda3\python.exe"
if defined PY_EXE goto :have_python

if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if defined PY_EXE goto :have_python

if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY_EXE goto :have_python

if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if defined PY_EXE goto :have_python

:: 4. Test py -3 or python in PATH
set "SYS_PY="
py -3 -c "import sys; print(sys.version)" >nul 2>nul
if not errorlevel 1 set "SYS_PY=py -3"
if defined SYS_PY goto :create_venv

python -c "import sys; print(sys.version)" >nul 2>nul
if not errorlevel 1 set "SYS_PY=python"
if defined SYS_PY goto :create_venv

:: 5. Install Python 3.11 via winget if no Python is installed
echo [Setup] Python 3.11 not found on your PC. Installing Python 3.11 via winget...
winget install Python.Python.3.11 --accept-package-agreements --accept-source-agreements
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "SYS_PY="%LOCALAPPDATA%\Programs\Python\Python311\python.exe""
if defined SYS_PY goto :create_venv

echo.
echo [Error] Could not locate Python 3.11 after installation.
echo Please close this window and double-click run_server.bat again, or install Python 3.11 from python.org.
pause
exit /b 1

:create_venv
echo [Setup] Creating local virtual environment in .venv ...
%SYS_PY% -m venv "%~dp0.venv"
if not exist "%~dp0.venv\Scripts\python.exe" goto :venv_error
set "PY_EXE=%~dp0.venv\Scripts\python.exe"
goto :have_python

:venv_error
echo [Error] Failed to create .venv virtual environment.
pause
exit /b 1

:have_python
echo [Info] Using Python: "%PY_EXE%"

:: 6. Check if required packages are installed
"%PY_EXE%" -c "import fastapi, faster_whisper, silero_vad, openai, yt_dlp" >nul 2>nul
if not errorlevel 1 goto :launch_server

echo.
echo [Setup] Installing required AI packages - first run only, please wait...
"%PY_EXE%" -m pip install --upgrade pip
"%PY_EXE%" -c "import torch" >nul 2>nul
if not errorlevel 1 goto :install_reqs

echo [Setup] Installing PyTorch with CUDA 12.4 support...
"%PY_EXE%" -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124

:install_reqs
echo [Setup] Installing requirements.txt...
"%PY_EXE%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto :pip_error
goto :launch_server

:pip_error
echo.
echo [Error] Package installation failed! See error messages above.
pause
exit /b 1

:launch_server
echo.
echo [Server] Starting RT-Subs Subtitle Server on http://127.0.0.1:8765 ...
cd /d "%~dp0server"
"%PY_EXE%" -m rtsubs.server

echo.
echo [Server] Process exited with code %errorlevel%.
pause
