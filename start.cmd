@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
powershell -NoExit -ExecutionPolicy Bypass -File ".\start.ps1" %*
