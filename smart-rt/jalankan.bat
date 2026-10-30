@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python belum terpasang atau belum masuk PATH. Install dari python.org dan centang "Add Python to PATH".
  pause & exit /b 1
)
if not exist .venv (
  echo Membuat virtual environment...
  python -m venv .venv || (echo Gagal membuat venv & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
echo Memasang dependensi...
python -m pip install -q -r requirements.txt || (echo Gagal pip install. Cek koneksi internet. & pause & exit /b 1)
echo.
echo Buka http://localhost:5000 di browser. Tekan Ctrl+C untuk berhenti.
python run.py
pause
