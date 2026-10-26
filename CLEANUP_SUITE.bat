@echo off
cd /d "%~dp0"
call ENV.bat
echo --- Project 1 ---
python project_1_documents\app\cleanup_project.py
echo --- Project 2 ---
python project_2_nl2sql\app\cleanup_project.py
echo --- Project 3 ---
python project_3_dashboard\app\cleanup_project.py
echo Done. Documents, databases and models were not touched.
pause