@echo off
cd /d "%~dp0"
if exist "EnglishDictation.exe" (
  start "" "EnglishDictation.exe"
  exit /b 0
)
python app.py
if errorlevel 1 pause
