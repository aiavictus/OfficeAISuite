@echo off
cd /d "%~dp0"
call ENV.bat
python VERIFY_SUITE.py %*
set "RC=%ERRORLEVEL%"
echo %CMDCMDLINE% | find /i "%~0" >nul && pause
exit /b %RC%