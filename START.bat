@echo off
setlocal EnableExtensions
chcp 65001 >nul
title GameStick Studio

:: Переход в директорию скрипта
cd /d "%~dp0"

echo ======================================================================
echo                 GameStick Studio - Запуск приложения
echo ======================================================================
echo.

:: 1. Проверка прав Администратора
net session >nul 2>&1
if %errorlevel% equ 0 goto :admin_ok

echo [INFO] Для работы с физическими дисками требуются права Администратора.
echo [INFO] Запрашиваем повышение привилегий UAC...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs"
exit /b

:admin_ok

:: 2. Поиск установленного Python (3.10+)
set "PYTHON_CMD="

py -3 -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
    goto :python_found
)

python -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    goto :python_found
)

python3 -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python3"
    goto :python_found
)

:python_not_found
echo.
echo ======================================================================
echo [ОШИБКА] Python 3.10 или новее не найден на вашем компьютере!
echo ======================================================================
echo Пожалуйста, установите Python:
echo 1. Скачайте установщик: https://www.python.org/downloads/
echo 2. ВАЖНО: В установщике обязательно отметьте галочку:
echo    [v] Add Python to PATH
echo.
set "OPEN_BROWSER="
set /p OPEN_BROWSER="Открыть страницу загрузки Python [Y/N]? "
if /i "%OPEN_BROWSER%"=="Y" start https://www.python.org/downloads/
pause
exit /b 1

:python_found

:: 3. Настройка виртуального окружения
set "VENV_DIR=.venv"
set "VENV_PYTHON=%~dp0%VENV_DIR%\Scripts\python.exe"

if exist "%VENV_PYTHON%" (
    set EXEC_CMD="%VENV_PYTHON%"
    goto :venv_ready
)

echo [INFO] Создание виртуального окружения [%VENV_DIR%]...
%PYTHON_CMD% -m venv "%~dp0%VENV_DIR%"

if exist "%VENV_PYTHON%" (
    set EXEC_CMD="%VENV_PYTHON%"
) else (
    echo [ВНИМАНИЕ] Не удалось создать .venv, используется системный Python.
    set EXEC_CMD=%PYTHON_CMD%
)

:venv_ready

:: 4. Проверка и установка зависимостей
if not exist "%~dp0requirements.txt" (
    echo.
    echo ======================================================================
    echo [ОШИБКА] Файл requirements.txt не найден!
    echo Путь поиска: %~dp0
    echo ======================================================================
    pause
    exit /b 1
)

%EXEC_CMD% -c "import PySide6, requests, PIL" >nul 2>&1
if not errorlevel 1 goto :deps_ready

echo [INFO] Первоначальная установка библиотек [PySide6, requests, Pillow]...
echo [INFO] Это может занять некоторое время, пожалуйста, подождите...
echo.

%EXEC_CMD% -m pip install --quiet --upgrade pip
%EXEC_CMD% -m pip install -r "%~dp0requirements.txt"

if errorlevel 1 (
    echo.
    echo ======================================================================
    echo [ОШИБКА] Не удалось установить зависимости из requirements.txt!
    echo Проверьте подключение к интернету и повторите запуск.
    echo ======================================================================
    pause
    exit /b 1
)

echo [INFO] Все компоненты успешно установлены!
echo.

:deps_ready

:: 5. Запуск приложения GameStick Studio
if not exist "%~dp0run.py" (
    echo.
    echo ======================================================================
    echo [ОШИБКА] Главный скрипт run.py не найден!
    echo Путь поиска: %~dp0
    echo ======================================================================
    pause
    exit /b 1
)

echo [INFO] Запуск GameStick Studio...
echo.

%EXEC_CMD% "%~dp0run.py"

if errorlevel 1 (
    echo.
    echo ======================================================================
    echo Приложение завершилось с ошибкой.
    echo ======================================================================
    pause
)