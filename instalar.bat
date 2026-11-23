@echo off
rem Sala de Ensaio - instalador (chama o instalar.ps1; aceita -Desinstalar, -SemDemo, -SemAbrir, -Biblioteca PASTA)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1" %*
echo.
pause
