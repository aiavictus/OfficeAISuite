@echo off
cd /d "%~dp0"
call ENV.bat
python project_3_dashboard\app\cleanup_project.py %*
pause