@echo off
cd /d "%~dp0"
call ENV.bat
start "" /b cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8110"
python project_3_dashboard\app\run.py --port 8110
pause