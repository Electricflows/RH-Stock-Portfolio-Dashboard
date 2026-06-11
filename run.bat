@echo off
cd /d "%~dp0"
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8501"') do taskkill /f /pid %%a >nul 2>&1
python -m streamlit run App.py
pause
