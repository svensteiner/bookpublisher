@echo off
setlocal
cd /d "%~dp0\.."

echo Building BookPublisher Windows app...
echo This requires pyinstaller. Install dev requirements first if needed:
echo   python -m pip install -r requirements-dev.txt

python -m PyInstaller ^
  --noconfirm ^
  --windowed ^
  --name BookPublisher ^
  --add-data "config.yaml;." ^
  --add-data "skills;skills" ^
  gui.py

echo.
echo Build output:
echo   dist\BookPublisher\BookPublisher.exe
endlocal
