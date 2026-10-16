@echo off
title IC Storage
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY=C:\Users\user\AppData\Local\Python\pythoncore-3.14-64\python.exe
rem keep-alive 300s: iPhone reuses idle connections (default 5s -> "network connection was lost" after taking a photo)
rem Port 80 = no ":8800" in the link ; if port 80 is busy fall back to 8800
set IC_PORT=80
netstat -ano | findstr /r /c:":80 .*LISTENING" >nul && set IC_PORT=8800
set URL=http://localhost
if not "%IC_PORT%"=="80" set URL=http://localhost:%IC_PORT%
echo ===============================================
echo   IC Storage starting...
echo   This PC : %URL%
if "%IC_PORT%"=="80" (echo   Phone   : http://icstorage.local) else (echo   Phone   : http://icstorage.local:%IC_PORT%)
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo   or IP   :%%a
echo   QR code : %URL%/connect
echo   Close this window to stop the server
echo ===============================================
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 4; Start-Process %URL%"
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port %IC_PORT% --timeout-keep-alive 300 --timeout-graceful-shutdown 3
echo.
echo Server stopped. See error above.
pause
