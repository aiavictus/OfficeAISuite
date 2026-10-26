@echo off
cd /d "%~dp0"
call ENV.bat
python project_2_nl2sql\app\cleanup_project.py %*
pause