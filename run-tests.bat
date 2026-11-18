@echo off
rem รัน unit test ทั้งหมด (ไม่ต่อเน็ต ไม่เขียน logs\ จริง) · ควรรันทุกครั้งหลังแก้ config.py หรือโค้ด ก่อนรีสตาร์ต agent
cd /d "%~dp0"
python -m unittest discover -s tests -v
pause
