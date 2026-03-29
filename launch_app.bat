@echo off
rem このコードは仮想環境を有効化して app.py を起動するための Windows 用ランチャーです。
setlocal

set "ROOT_DIR=%~dp0"

call "%ROOT_DIR%\.venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERROR] 仮想環境の有効化に失敗しました。
    pause
    exit /b 1
)

python "%ROOT_DIR%app.py"
if errorlevel 1 (
    echo.
    echo [ERROR] app.py の起動に失敗しました。
    pause
    exit /b 1
)

endlocal
