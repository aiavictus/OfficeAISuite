@echo off
set "SUITE_ROOT=%~dp0"
if "%SUITE_ROOT:~-1%"=="\" set "SUITE_ROOT=%SUITE_ROOT:~0,-1%"
set "PATH=%SUITE_ROOT%\application\python;%SUITE_ROOT%\application\llm_runtime\llama.cpp;%SUITE_ROOT%\application\ocr\tesseract;%PATH%"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
echo Suite environment active: %SUITE_ROOT%