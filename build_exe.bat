@echo off
echo ============================================
echo  BookPublisher - EXE Build
echo ============================================
echo.

where pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo PyInstaller nicht gefunden. Installiere...
    pip install pyinstaller
)

echo Erstelle EXE...
python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "BookPublisher" ^
    --add-data "config.yaml;." ^
    --add-data "skills;skills" ^
    --add-data "modules;modules" ^
    --exclude-module pytest ^
    --exclude-module _pytest ^
    --exclude-module IPython ^
    --exclude-module notebook ^
    --exclude-module matplotlib ^
    --exclude-module scipy ^
    gui.py

echo.
if exist "dist\BookPublisher.exe" (
    echo FERTIG!
    echo   dist\BookPublisher.exe
    for %%A in ("dist\BookPublisher.exe") do echo   Groesse: %%~zA Bytes
    echo.
    echo Naechster Schritt: dist\BookPublisher.exe testen
) else (
    echo FEHLER: EXE wurde nicht erstellt.
    echo Pruefen: pip install -r requirements.txt
)
pause
