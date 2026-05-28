@echo off
setlocal EnableDelayedExpansion
set PYTHONUTF8=1

title Jarvis — AI Assistant

echo ============================================================
echo  JARVIS — Local AI Voice Assistant
echo ============================================================
echo.

:: ── 1. Rilevamento Chiave DeepSeek API ───────────────────────────
echo [1/4] Verifica configurazione DeepSeek...
set "TEMP_KEY="
if exist "%~dp0.env" (
    for /f "usebackq tokens=1,2 delims==" %%a in ("%~dp0.env") do (
        if "%%a"=="DEEPSEEK_API_KEY" (
            set "TEMP_KEY=%%b"
        )
    )
)
if "%TEMP_KEY%"=="" (
    echo [ERRORE] Chiave API DeepSeek non configurata in %~dp0.env.
    echo Per favore, inserisci la tua DEEPSEEK_API_KEY nel file .env per far funzionare l'IA.
    pause
    exit /b 1
)
if "%TEMP_KEY%"=="your_deepseek_api_key_here" (
    echo [ERRORE] Chiave API DeepSeek ancora impostata al valore di default.
    echo Per favore, modifica il file %~dp0.env sostituendo 'your_deepseek_api_key_here' con la tua vera chiave DeepSeek.
    pause
    exit /b 1
)
echo       DeepSeek configurato correttamente.
echo.

:: ── 2. Avvio kiwix-serve (Wikipedia Italiana) ────────────────────
echo [2/4] Avvio di kiwix-serve...
set "KIWIX_ZIM_PATH="
for %%f in ("%~dp0wikipedia_it_all_nopic_*.zim") do (
    set "KIWIX_ZIM_PATH=%%f"
)
if defined KIWIX_ZIM_PATH (
    start /B "" "%~dp0kiwix-serve.exe" --port 8888 "!KIWIX_ZIM_PATH!"
    echo       kiwix-serve avviato sulla porta 8888 con !KIWIX_ZIM_PATH!.
) else (
    echo       [ATTENZIONE] File ZIM wikipedia_it_all_nopic_*.zim non trovato in %~dp0.
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
