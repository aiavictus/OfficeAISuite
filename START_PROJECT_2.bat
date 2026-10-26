@echo off
cd /d "%~dp0"
call ENV.bat
start "" /b cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8100"
python project_2_nl2sql\app\run.py --port 8100
pause