@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"

rem ------------------------------------------------------------------
rem Smart launcher for BookPublisher.
rem
rem Priority order — first match wins:
rem   1. BookPublisher.exe in the same folder (preferred customer path).
rem   2. python gui.py (source-repo developer path).
rem   3. Friendly German error message with concrete next step.
rem
rem Never shows a raw Python traceback to the end user.
rem ------------------------------------------------------------------

if exist "%~dp0BookPublisher.exe" (
    start "" "%~dp0BookPublisher.exe"
    endlocal
    exit /b 0
)

if exist "%~dp0gui.py" (
    where python >nul 2>&1
    if not errorlevel 1 (
        python "%~dp0gui.py"
        if errorlevel 1 (
            echo.
            echo FEHLER: BookPublisher konnte nicht gestartet werden.
            echo.
            echo Bitte pruefe:
            echo   1. Liegt 'config.yaml' im gleichen Ordner wie 'gui.py'?
            echo   2. Sind die Python-Pakete installiert?
            echo      Im Terminal:  pip install -r requirements.txt
            echo.
            pause
            endlocal
            exit /b 1
        )
        endlocal
        exit /b 0
    )
    echo.
    echo Python ist auf diesem Computer nicht installiert.
    echo.
    echo Du hast zwei Moeglichkeiten:
    echo   A) Lade die fertige BookPublisher.exe von der Homepage.
    echo      Sie braucht kein Python.
    echo   B) Installiere Python 3.10 oder neuer von https://www.python.org
    echo      und fuehre danach diese Datei nochmal aus.
    echo.
    pause
    endlocal
    exit /b 2
)

echo.
echo FEHLER: Dieser Ordner enthaelt weder 'BookPublisher.exe' noch 'gui.py'.
echo.
echo Es sieht aus, als waere das ZIP nicht vollstaendig entpackt.
echo Bitte entpacke 'BookPublisher.zip' komplett auf den Desktop und
echo starte diese Datei dort erneut.
echo.
pause
endlocal
exit /b 3
