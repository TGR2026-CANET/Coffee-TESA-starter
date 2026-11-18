@echo off
rem ตลาดตาม .env (เช่น http://202.29.227.22:8000) · log ที่ logs\ (โฟลเดอร์ที่ pack.py ส่งกรรมการ)
cd /d "%~dp0"
set ENV_FILE=
set LOG_DIR=
python main.py --forever
pause
