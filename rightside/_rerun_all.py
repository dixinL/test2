import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(BASE_DIR), 'general-data'))

import leadersdb
import run as runmod

DATES = ['2026-08-27', '2026-08-28', '2026-08-31', '2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04', '2026-09-07', '2026-09-08', '2026-09-09', '2026-09-10', '2026-09-11', '2026-09-14', '2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18', '2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24', '2026-09-28', '2026-09-29', '2026-09-30', '2026-10-08', '2026-10-09', '2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16', '2026-10-19', '2026-10-20', '2026-10-21', '2026-10-22', '2026-10-23', '2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30']

conn = leadersdb.connect()
try:
    for d in DATES:
        t0 = time.time()
        print('[batch] === ' + d + ' ===', flush=True)
        runmod.run_all(conn, d)
        print('[batch] ' + d + ' done %.1fs' % (time.time() - t0), flush=True)
finally:
    conn.close()
print('[batch] ALL DONE', flush=True)
