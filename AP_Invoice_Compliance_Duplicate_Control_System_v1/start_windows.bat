@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
pause

