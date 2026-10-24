@echo off
rem Runs Live Caption from the virtualenv on Windows. The CUDA DLLs are set up inside live_caption.py.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Virtualenv not found. Run setup.ps1 first, see README.md 1>&2
  exit /b 1
)

".venv\Scripts\python.exe" live_caption.py %*
