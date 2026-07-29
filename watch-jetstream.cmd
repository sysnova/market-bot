@echo off
setlocal
set "ROOT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT_DIR%scripts\windows\watch-jetstream.ps1" %*
exit /b %ERRORLEVEL%
