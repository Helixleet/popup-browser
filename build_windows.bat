@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python не найден! Установите Python 3.10+ с https://python.org ^(галочка "Add to PATH"^).
  pause
  exit /b 1
)
echo Устанавливаю зависимости (PySide6 + PyInstaller)...
python -m pip install --disable-pip-version-check -r requirements.txt
echo.
echo Собираю PopupBrowser.exe (займёт 2-5 минут)...
python -m PyInstaller --noconfirm --clean --onedir --windowed --name PopupBrowser main.py
if errorlevel 1 (
  echo.
  echo Ошибка сборки — подробности выше.
  pause
  exit /b 1
)
echo.
echo ============================================================
echo  Готово! Билд: dist\PopupBrowser\PopupBrowser.exe
echo  Папку dist\PopupBrowser можно перенести на любой ПК —
echo  это портативная версия (данные хранятся в data\ рядом с exe).
echo ============================================================
pause
