@echo off
title IC Storage
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY=C:\Users\user\AppData\Local\Python\pythoncore-3.14-64\python.exe
echo ===============================================
echo   IC Storage starting...
echo   This PC : http://localhost:8800
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo   This PC IP:%%a
echo   Phone (same Wi-Fi): http://[IP above]:8800
echo   Close this window to stop the server
echo ===============================================
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 4; Start-Process http://localhost:8800"
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8800 --timeout-graceful-shutdown 3
echo.
echo Server stopped. See error above.
pause
