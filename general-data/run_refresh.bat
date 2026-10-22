@echo off
cd /d %~dp0
python -u refresh.py > refresh_out.log 2> refresh_err.log

