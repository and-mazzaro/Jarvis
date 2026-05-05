@echo off
setlocal EnableDelayedExpansion

title Jarvis — Setup

echo ============================================================
echo  JARVIS — One-time Setup
echo ============================================================
echo.

:: ── Check Python 3.11 ──────────────────────────────────────────
echo [1/5] Checking Python version...

set "PYTHON_CMD="

:: Try "py -3.11"
py -3.11 --version > nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.11"
    goto :found
)

:: Try "python"
for /f "tokens=2" %%v in ('python --version 2^>nul') do set "VER=%%v"
if defined VER (
    echo %VER% | findstr /R "^3\.11" > nul
    if not errorlevel 1 (
        set "PYTHON_CMD=python"
        goto :found
    )
)

echo [ERROR] Python 3.11 not detected correctly.
echo         Current 'python' version is: %VER%
echo         Please ensure Python 3.11 is installed and in your PATH.
pause
exit /b 1

:found
echo       Using: %PYTHON_CMD%
%PYTHON_CMD% --version
echo.

:: ── Create venv ────────────────────────────────────────────────
echo [2/5] Creating virtual environment (.venv)...
if exist ".venv" (
    echo       .venv already exists — skipping.
) else (
    %PYTHON_CMD% -m venv .venv
    echo       .venv created.
)

echo.

:: ── Activate and install dependencies ──────────────────────────
echo [3/5] Installing Python dependencies...
call .venv\Scripts\activate.bat
pip install --upgrade pip --quiet
pip install -r backend\requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed. Check your internet connection.
    pause
    exit /b 1
)
echo       Dependencies installed.
echo.

:: ── Pull Ollama model ──────────────────────────────────────────
echo [4/5] Pulling Ollama model (mistral:7b-instruct)...
ollama --version > nul 2>&1
if errorlevel 1 (
    echo [WARNING] Ollama not found in PATH.
    echo           Download from: https://ollama.com
    echo           After installing, run: ollama pull mistral:7b-instruct
) else (
    ollama pull mistral:7b-instruct
    echo       Model ready.
)
echo.

:: ── Install Node dependencies ──────────────────────────────────
echo [5/6] Installing Electron/Frontend dependencies...
where npm > nul 2>&1
if errorlevel 1 (
    echo [WARNING] Node.js/npm not found. Please install Node.js to use the UI.
) else (
    npm install
)
echo.

:: ── Create required directories ────────────────────────────────
echo [6/6] Creating project directories...
if not exist "chroma_db"  mkdir chroma_db
if not exist "documents"  mkdir documents
echo       Done.
echo.

echo.
echo [7/7] Configurazione database Supabase...
python backend/setup_supabase.py
echo.

:: ── Final instructions ─────────────────────────────────────────
echo ============================================================
echo  SETUP COMPLETE
echo ============================================================
echo.
echo  IMPORTANTE: Assicurati di aver configurato il file .env
echo  con le tue credenziali Supabase prima di avviare.
echo.
echo  Next steps:
echo.
echo  1. Download TTS model files into the project root:
echo       kokoro-v1.0.onnx
echo       voices-v1.0.bin
echo     From: https://huggingface.co/hexgrad/Kokoro-82M
echo.
echo  2. (Optional) Download a Wikipedia ZIM file:
echo     https://wiki.kiwix.org/wiki/Content_in_all_languages
echo     Then set: set KIWIX_ZIM_PATH=C:\path\to\file.zim
echo.
echo  3. Run the assistant:
echo       Double-click launch.bat
echo       OR open Electron: npm start
echo.
pause
