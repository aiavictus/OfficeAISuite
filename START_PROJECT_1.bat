@echo off
cd /d "%~dp0"
call ENV.bat
start "" /b cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8090"
python project_1_documents\app\run.py --port 8090
pause