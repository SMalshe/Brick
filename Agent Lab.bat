@echo off
rem Double-click this file (Windows) to open the Agent Lab in your browser.
cd /d "%~dp0"

set "PY=C:\Users\Lab User\SAIL\python\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -c "import requests, pptx, openpyxl" 1>nul 2>nul
if errorlevel 1 (
  echo Installing the agent's Python packages ^(one time^)...
  "%PY%" -m pip install --quiet requests python-pptx openpyxl
)

"%PY%" -m webui.server
pause
