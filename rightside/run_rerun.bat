@echo off
cd /d %~dp0
python -u _rerun_all.py > rerun_out.log 2> rerun_err.log

