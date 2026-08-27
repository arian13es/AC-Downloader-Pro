@echo off
title AC-Downloader Pro - Standalone Builder
echo ===================================================
echo AC-Downloader Pro - Standalone Builder
echo ===================================================
echo.

if not exist "tools" mkdir tools

echo.
echo [0/2] Closing any running instance...
taskkill /F /IM AC-Downloader.exe >nul 2>&1

echo.
echo [1/2] Installing required build tools using UV...
uv venv .venv
uv pip install pyinstaller pywin32 customtkinter darkdetect requests urllib3 pymupdf pywebview

echo.
echo [2/2] Freezing Python code into Standalone Executable...
uv run pyinstaller -y --noconsole --onedir --icon "installer\app.ico" --version-file "installer\version_info.txt" --manifest "installer\app.manifest" --hidden-import=fitz --hidden-import=pymupdf --collect-all webview --hidden-import=clr_loader --hidden-import=pythonnet --hidden-import=webview.platforms.winforms --hidden-import=webview.platforms.edgechromium --add-data "tools;tools" --add-data "installer/app.ico;." --name "AC-Downloader" main.py

IF %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] PyInstaller failed! Please check the error above.
    pause
    exit /b
)

echo.
echo ===================================================
echo [SUCCESS] Build complete! 
echo Your Standalone EXE is located in the "dist" folder.
echo ===================================================
exit /b 0
