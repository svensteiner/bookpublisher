@echo off
setlocal
cd /d "%~dp0\.."

echo Building BookPublisher CLI (console) EXE...
echo This requires pyinstaller. Install dev requirements first if needed:
echo   python -m pip install -r requirements-dev.txt

python -m PyInstaller ^
  --noconfirm ^
  --console ^
  --name BookPublisher-cli ^
  --add-data "config.yaml;." ^
  --add-data "skills;skills" ^
  main.py

echo.
echo Build output:
echo   dist\BookPublisher-cli\BookPublisher-cli.exe
echo.
echo Smoke test (no input, no API call):
echo   dist\BookPublisher-cli\BookPublisher-cli.exe smoke
endlocal
