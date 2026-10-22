"""状态观察记录：rightside 每日三态存 report/rightside_states.csv。

- save_states(): 按判定日幂等重写当日行（重复运行无副作用）
- load_day(): 读取某日全量行（含 name/sw2，供 analysis-hub 总览消费）

注意：CSV 只是每日观察记录（CI 全量重建库时靠 git 里的它跨日累积）；
「状态起点」不在本文件回溯，而是由 rightside.state_start_real()
从基础数据（leaders.db 日线）现算价格结构连续段。
"""
from __future__ import annotations

import csv
import os

STATES_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'report', 'rightside_states.csv')


def save_states(target: str, entries) -> None:
    """按判定日幂等重写当日状态行。entries: [(code, name, sw2, state)]。"""
    history = {}
    if os.path.exists(STATES_CSV):
        with open(STATES_CSV, 'r', encoding='utf-8', newline='') as f:
            for row in csv.DictReader(f):
                if row['date'] != target:
                    history.setdefault(row['date'], []).append(
                        (row['code'], row.get('name', ''),
                         row.get('sw2', ''), row['state']))
    history[target] = [(c, n, s2, s) for c, n, s2, s in entries]
    os.makedirs(os.path.dirname(STATES_CSV), exist_ok=True)
    with open(STATES_CSV, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['date', 'code', 'name', 'sw2', 'state'])
        for d in sorted(history):
            for code, name, sw2, state in sorted(history[d]):
                w.writerow([d, code, name, sw2, state])


def load_day(target: str):
    """返回某日全部行 [(code, name, sw2, state)]；无该日记录返回 []。"""
    out = []
    if not os.path.exists(STATES_CSV):
        return out
    with open(STATES_CSV, 'r', encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            if row['date'] == target:
                out.append((row['code'], row.get('name', ''),
                            row.get('sw2', ''), row['state']))
    return out
