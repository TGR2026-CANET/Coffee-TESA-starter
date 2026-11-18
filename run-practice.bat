@echo off
rem ตลาดตาม .env-practice (เช่น https://pooh-coffee-tesa.fly.dev) · log แยกที่ logs-practice\
cd /d "%~dp0"
set ENV_FILE=.env-practice
set LOG_DIR=logs-practice
python main.py --forever
pause
