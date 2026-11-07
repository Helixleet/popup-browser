@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python не найден! Установите Python 3.10+ с https://python.org ^(галочка "Add to PATH"^) и перезапустите файл.
  pause
  exit /b 1
)
echo Первый запуск может занять 1-2 минуты (установка PySide6)...
python -m pip install --disable-pip-version-check -r requirements.txt
python main.py %*
