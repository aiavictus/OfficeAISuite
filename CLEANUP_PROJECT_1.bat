@echo off
cd /d "%~dp0"
call ENV.bat
python project_1_documents\app\cleanup_project.py %*
pause