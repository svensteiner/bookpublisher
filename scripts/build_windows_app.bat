@echo off
setlocal
cd /d "%~dp0\.."

echo Building BookPublisher Windows app...
echo This requires pyinstaller. Install dev requirements first if needed:
echo   python -m pip install -r requirements-dev.txt

python -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name BookPublisher ^
  --add-data "config.yaml;." ^
  --add-data "skills;skills" ^
  --add-data "modules;modules" ^
  --exclude-module pytest ^
  --exclude-module _pytest ^
  --exclude-module hypothesis ^
  --exclude-module IPython ^
  --exclude-module jupyter ^
  --exclude-module notebook ^
  --exclude-module pandas ^
  --exclude-module numpy ^
  --exclude-module pyarrow ^
  --exclude-module matplotlib ^
  --exclude-module scipy ^
  --exclude-module sklearn ^
  --exclude-module openpyxl ^
  --exclude-module lxml ^
  --exclude-module jinja2 ^
  gui.py

echo.
echo Build output:
echo   dist\BookPublisher.exe
endlocal
