"""rightside 数据访问层（纯读，不写库）。

数据源：
- test2/general-data/leaders.db  个股日线/复权因子/财务/龙头维表/指数日线
- test2/general-data/env/        test1 环境快照 CSV（envfetch.py 拉取）
    env_YYYY-MM-DD.csv   板块场景/宽度/拥挤度 + 市场判断（MARKET 首行）
    trend_YYYY-MM-DD.csv 行业多窗口趋势（sw_trend_summary 副本）
"""
from __future__ import annotations

import csv
import os
from datetime import date as _date

import leadersdb

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # test2/
ENV_DIR = os.path.join(BASE_DIR, 'general-data', 'env')
INDEX_CODE = 'sh.000001'

# test2 个股所需指标窗口的最长回看（120 日新低 + 指标预热余量）
MIN_ROWS = 130


def get_target_date(conn) -> str | None:
    """默认判定日 = 近 5 个交易日中覆盖股票数最多的"共识日"。

    不能取全局 MAX：当日仅少数股票先入库时（baostock 盘后陆续可得），
    以多数股票的共同最新交易日为准。"""
    row = conn.execute("""
        SELECT trade_date FROM (
            SELECT trade_date, COUNT(DISTINCT code) AS n
            FROM daily GROUP BY trade_date
            ORDER BY trade_date DESC LIMIT 5
        ) ORDER BY n DESC LIMIT 1
    """).fetchone()
    return row['trade_date'] if row else None


def get_prev_trade_dates(conn, target: str, n: int) -> list[str]:
    """target 日（含）往前 n 个交易日（升序）。"""
    rows = conn.execute(
        'SELECT DISTINCT trade_date FROM daily WHERE trade_date <= ?'
        ' ORDER BY trade_date DESC LIMIT ?', (target, n)).fetchall()
    return sorted(r['trade_date'] for r in rows)


def load_leaders(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        'SELECT code, exchange, name, sw1, sw2 FROM leaders ORDER BY code')]


def load_daily(conn, code: str, target: str) -> list[dict]:
    """某股 target（含）之前全部日线（升序，含停牌行）。"""
    return [dict(r) for r in conn.execute(
        'SELECT trade_date, open, high, low, close, preclose, volume, turn,'
        '       tradestatus, is_st, pct_chg'
        ' FROM daily WHERE code = ? AND trade_date <= ? ORDER BY trade_date',
        (code, target))]


def load_financial(conn, code: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        'SELECT year, quarter, pub_date, roe_avg, yoy_ni, liability_to_asset'
        ' FROM financial WHERE code = ? ORDER BY year, quarter', (code,))]


def load_index_close(conn, target: str) -> list[tuple[str, float]]:
    """上证综指 target（含）之前收盘序列（升序）。"""
    rows = conn.execute(
        'SELECT trade_date, close FROM index_daily'
        ' WHERE code = ? AND trade_date <= ? ORDER BY trade_date',
        (INDEX_CODE, target)).fetchall()
    return [(r['trade_date'], r['close']) for r in rows]


# ---------- 环境快照 CSV ----------

def _env_dates() -> list[str]:
    if not os.path.isdir(ENV_DIR):
        return []
    return sorted(
        f[4:-4] for f in os.listdir(ENV_DIR)
        if f.startswith('env_') and f.endswith('.csv'))


def load_env(target: str) -> tuple[str | None, dict | None, dict[str, dict]]:
    """取 target（含）之前最新一份 env_summary CSV。

    返回 (env_date, market_row, industries_by_name)。
    market_row 为 MARKET 汇总行；industries_by_name 以 sw_name 为键。
    无任何可用快照返回 (None, None, {})。
    """
    dates = [d for d in _env_dates() if d <= target]
    if not dates:
        return None, None, {}
    d = dates[-1]
    path = os.path.join(ENV_DIR, 'env_{}.csv'.format(d))
    market, industries = None, {}
    with open(path, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            if row.get('sw_code') == 'MARKET':
                market = row
            else:
                industries[row.get('sw_name', '')] = row
    return d, market, industries


def _parse_dir_field(s: str) -> tuple[str, float, float]:
    """解析 'UP(+0.0127 | +0.0079)' -> ('UP', 0.0127, 0.0079)。"""
    s = (s or '').strip()
    direction = s.split('(', 1)[0]
    slope = r2 = 0.0
    if '(' in s and s.endswith(')'):
        try:
            a, b = s[s.index('(') + 1:-1].split('|')
            slope, r2 = float(a), float(b)
        except (ValueError, IndexError):
            pass
    return direction, slope, r2


def load_trend(target: str) -> tuple[str | None, dict[str, dict]]:
    """取 target（含）之前最新一份 trend CSV，按行业名称索引。
    每行附加 dir_20/dir_60（方向词）与 slope_20/slope_60。"""
    if not os.path.isdir(ENV_DIR):
        return None, {}
    dates = sorted(
        f[6:-4] for f in os.listdir(ENV_DIR)
        if f.startswith('trend_') and f.endswith('.csv') and f[6:-4] <= target)
    if not dates:
        return None, {}
    path = os.path.join(ENV_DIR, 'trend_{}.csv'.format(dates[-1]))
    out = {}
    with open(path, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            name = (row.get('行业名称') or '').strip()
            if not name:
                continue
            d20, s20, _ = _parse_dir_field(row.get('方向_20天', ''))
            d60, s60, _ = _parse_dir_field(row.get('方向_60天', ''))
            row['dir_20'], row['slope_20'] = d20, s20
            row['dir_60'], row['slope_60'] = d60, s60
            out[name] = row
    return dates[-1], out
