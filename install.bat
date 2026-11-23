@echo off
rem Riffroom - installer (calls install.ps1; accepts -AI, -GPU, -Language pt, -Library FOLDER, -NoDemo, -NoOpen, -NoClaude, -Uninstall)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
