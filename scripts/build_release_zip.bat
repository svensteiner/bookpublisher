@echo off
setlocal
cd /d "%~dp0\.."

echo Building BookPublisher.zip for distribution...
echo.

python -m modules.release_packager %*

if errorlevel 1 (
  echo.
  echo FEHLER beim Paket-Build. Logs oben pruefen.
  exit /b 1
)

echo.
echo Fertig. Hochladen auf die Homepage als BookPublisher.zip.
endlocal
