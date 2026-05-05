@echo off
setlocal EnableDelayedExpansion
set PYTHONUTF8=1

title Jarvis — AI Assistant

echo ============================================================
echo  JARVIS — Local AI Voice Assistant
echo ============================================================
echo.

:: ── 1. Start Ollama ────────────────────────────────────────────
echo [1/4] Avvio di Ollama...
start /B "" ollama serve
timeout /t 3 /nobreak > nul
echo       Ollama avviato.
echo.

:: ── 2. Avvio kiwix-serve (Wikipedia Italiana) ────────────────────
echo [2/4] Avvio di kiwix-serve...
set "KIWIX_ZIM_PATH=%~dp0wikipedia_it_all_nopic_2026-02.zim"
if exist "%KIWIX_ZIM_PATH%" (
    start /B "" "kiwix-serve.exe" --port 8888 "%KIWIX_ZIM_PATH%"
    echo       kiwix-serve avviato sulla porta 8888.
) else (
    echo       [ATTENZIONE] File ZIM non trovato in %KIWIX_ZIM_PATH%
    echo       Wikipedia offline non sarà disponibile.
)
echo.

:: ── 3. Attivazione Python venv ────────────────────────────────────
echo [3/4] Attivazione ambiente Python...
if not exist ".venv\Scripts\activate.bat" (
    echo [ERRORE] .venv non trovato. Esegui prima setup.bat.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
echo       Ambiente virtuale attivo.
echo.

:: ── 4. Avvio Jarvis (Backend e UI) ─────────────────────────────
echo [4/4] Avvio di Jarvis in corso...
echo       Backend e Interfaccia in avvio. Tieni aperta questa finestra.
echo       Premi Ctrl+C qui per fermare tutto.
echo.

:: Sincronizza i file della UI
call npm run copy-dist

:: Avvia l'interfaccia Electron in una nuova finestra
:: Impostiamo una variabile per dire a Electron di non riavviare il backend
set JARVIS_CONSOLE=1
start "" npm start

:: Avvia il backend Python usando il percorso assoluto del venv
"%~dp0.venv\Scripts\python.exe" backend\main.py

:: Keep window open if it crashes
if errorlevel 1 (
    echo.
    echo [ERROR] Backend exited with an error. Check the log above.
    pause
)
