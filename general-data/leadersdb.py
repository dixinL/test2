"""general-data SQLite 数据访问层（test2 全仓唯一基础数据库）。

表：
- leaders       龙头维表（来自 申万二级行业龙头_修正版.csv，UPSERT）
- daily         个股日线（不复权原始价 + 估值 + 状态，INSERT OR IGNORE 增量幂等）
- adjust_factor 复权因子（INSERT OR IGNORE 增量幂等，现算前/后复权的基础）
- financial     季频财务（UPSERT 覆盖，财报数据可能重述）
- index_daily   指数日线（上证综指，大盘环境判定用，INSERT OR IGNORE 幂等）

约定：
- code 统一为纯数字 6 位（如 000998），exchange ∈ {SH, SZ}。
- daily/adjust_factor 历史行永不重写：存不复权价，除权只更新因子表。
- financial 主键 (code, year, quarter) 冲突即覆盖（重述以最新为准）。
- 重复运行"新增 0 行"即数据就绪。
"""
from __future__ import annotations

import csv
import os
import sqlite3
from datetime import datetime
from typing import Iterable, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'leaders.db')
CSV_PATH = os.path.join(os.path.dirname(BASE_DIR), '申万二级行业龙头_修正版.csv')

# financial 回填的最早报告期（第一阶段口径：2020 年起）
EARLIEST_YEAR, EARLIEST_QUARTER = 2020, 1


def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    """打开数据库连接，确保目录存在。"""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """创建全部表（幂等，可重复调用）。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS leaders (
            code          TEXT PRIMARY KEY,
            exchange      TEXT NOT NULL,
            name          TEXT NOT NULL,
            sw1           TEXT,
            sw2           TEXT,
            leader_type   TEXT,
            moat          TEXT,
            margin_2023   REAL,
            margin_2024   REAL,
            margin_2025   REAL,
            roe_2023      REAL,
            roe_2024      REAL,
            roe_2025      REAL,
            updated_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS daily (
            code        TEXT NOT NULL,
            trade_date  TEXT NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            preclose    REAL,
            volume      REAL,
            amount      REAL,
            turn        REAL,
            pct_chg     REAL,
            tradestatus INTEGER,
            is_st       INTEGER,
            pe_ttm      REAL,
            pb_mrq      REAL,
            ps_ttm      REAL,
            pcf_ncf_ttm REAL,
            PRIMARY KEY (code, trade_date)
        );

        CREATE TABLE IF NOT EXISTS adjust_factor (
            code               TEXT NOT NULL,
            divid_operate_date TEXT NOT NULL,
            fore_factor        REAL,
            back_factor        REAL,
            adjust_factor      REAL,
            PRIMARY KEY (code, divid_operate_date)
        );

        CREATE TABLE IF NOT EXISTS financial (
            code              TEXT NOT NULL,
            year              INTEGER NOT NULL,
            quarter           INTEGER NOT NULL,
            pub_date          TEXT,
            stat_date         TEXT,
            roe_avg           REAL,
            gp_margin         REAL,
            np_margin         REAL,
            net_profit        REAL,
            eps_ttm           REAL,
            yoy_ni            REAL,
            yoy_asset         REAL,
            liability_to_asset REAL,
            updated_at        TEXT,
            PRIMARY KEY (code, year, quarter)
        );

        CREATE TABLE IF NOT EXISTS index_daily (
            code        TEXT NOT NULL,
            trade_date  TEXT NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      REAL,
            amount      REAL,
            pct_chg     REAL,
            PRIMARY KEY (code, trade_date)
        );
        """)
    conn.commit()


def _insert_ignore(conn, table, cols, placeholders, rows) -> int:
    """通用 INSERT OR IGNORE 批量写入，返回新增行数。"""
    rows = list(rows)
    if not rows:
        return 0
    before = conn.total_changes
    conn.executemany(
        'INSERT OR IGNORE INTO {} {} VALUES {}'.format(table, cols, placeholders),
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def _to_float(v):
    """CSV/接口空串转 None，其余转 float。"""
    if v is None or str(v).strip() == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------- leaders 维表 ----------

def upsert_leader(conn, code, exchange, name, sw1=None, sw2=None,
                  leader_type=None, moat=None, margins=None, roes=None) -> None:
    """写入/更新龙头维表。margins/roes 为 {'2023': x, ...} 形式。"""
    margins = margins or {}
    roes = roes or {}
    now = datetime.now().isoformat(timespec='seconds')
    conn.execute("""
        INSERT INTO leaders (code, exchange, name, sw1, sw2, leader_type, moat,
                             margin_2023, margin_2024, margin_2025,
                             roe_2023, roe_2024, roe_2025, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            exchange    = excluded.exchange,
            name        = excluded.name,
            sw1         = COALESCE(excluded.sw1, leaders.sw1),
            sw2         = COALESCE(excluded.sw2, leaders.sw2),
            leader_type = COALESCE(excluded.leader_type, leaders.leader_type),
            moat        = COALESCE(excluded.moat, leaders.moat),
            margin_2023 = COALESCE(excluded.margin_2023, leaders.margin_2023),
            margin_2024 = COALESCE(excluded.margin_2024, leaders.margin_2024),
            margin_2025 = COALESCE(excluded.margin_2025, leaders.margin_2025),
            roe_2023    = COALESCE(excluded.roe_2023, leaders.roe_2023),
            roe_2024    = COALESCE(excluded.roe_2024, leaders.roe_2024),
            roe_2025    = COALESCE(excluded.roe_2025, leaders.roe_2025),
            updated_at  = excluded.updated_at
        """,
        (code, exchange, name, sw1, sw2, leader_type, moat,
         _to_float(margins.get('2023')), _to_float(margins.get('2024')),
         _to_float(margins.get('2025')),
         _to_float(roes.get('2023')), _to_float(roes.get('2024')),
         _to_float(roes.get('2025')), now),
    )


def import_leaders_from_csv(conn, csv_path: str = CSV_PATH) -> int:
    """从龙头 CSV 幂等导入维表。返回导入行数。"""
    n = 0
    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            raw = str(row.get('股票代码', '')).strip().lower()
            if len(raw) < 8 or raw[2:8].isdigit() is False:
                continue
            exchange = raw[:2].upper()
            code = raw[2:8]
            upsert_leader(
                conn, code, exchange, str(row.get('公司名称', '')).strip(),
                sw1=str(row.get('申万一级行业', '')).strip() or None,
                sw2=str(row.get('申万二级行业', '')).strip() or None,
                leader_type=str(row.get('龙头类型', '')).strip() or None,
                moat=str(row.get('核心壁垒', '')).strip() or None,
                margins={'2023': row.get('2023_毛利率%'), '2024': row.get('2024_毛利率%'),
                         '2025': row.get('2025_毛利率%')},
                roes={'2023': row.get('2023_ROE%'), '2024': row.get('2024_ROE%'),
                      '2025': row.get('2025_ROE%')},
            )
            n += 1
    conn.commit()
    return n


def get_all_leaders(conn) -> list[sqlite3.Row]:
    """全部龙头（按 code 升序）。"""
    return conn.execute(
        'SELECT code, exchange, name, sw1, sw2 FROM leaders ORDER BY code'
    ).fetchall()


# ---------- daily / adjust_factor ----------

def upsert_daily_rows(conn, rows: Iterable[tuple]) -> int:
    """批量写日线。rows 每项与 daily 表列序一致（code 起 17 列）。返回新增行数。"""
    return _insert_ignore(
        conn, 'daily',
        '(code, trade_date, open, high, low, close, preclose, volume, amount,'
        ' turn, pct_chg, tradestatus, is_st, pe_ttm, pb_mrq, ps_ttm, pcf_ncf_ttm)',
        '(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        rows,
    )


def upsert_adjust_factors(conn, rows: Iterable[tuple]) -> int:
    """批量写复权因子。rows 每项 (code, divid_operate_date, fore, back, adj)。"""
    return _insert_ignore(
        conn, 'adjust_factor',
        '(code, divid_operate_date, fore_factor, back_factor, adjust_factor)',
        '(?, ?, ?, ?, ?)',
        rows,
    )


def get_max_trade_date(conn, code: str) -> Optional[str]:
    """某股在 daily 中的最新交易日；无则 None。"""
    row = conn.execute(
        'SELECT MAX(trade_date) AS d FROM daily WHERE code = ?', (code,)
    ).fetchone()
    return row['d'] if row and row['d'] else None


def get_adjust_factors(conn, code: str) -> list[sqlite3.Row]:
    """某股全部复权因子，按除权日升序。"""
    return conn.execute(
        'SELECT divid_operate_date, fore_factor, back_factor, adjust_factor'
        ' FROM adjust_factor WHERE code = ? ORDER BY divid_operate_date ASC',
        (code,),
    ).fetchall()


def adjusted_price(price: float, trade_date: str, factors: list[sqlite3.Row],
                   mode: str = 'qfq') -> float:
    """复权现算（纯函数，baostock 口径）。

    factors 为 get_adjust_factors 的升序结果。规则：trade_date >=
    最近一次除权日 divid_operate_date 的因子生效（baostock 因子自除权日
    当天起对新价格生效）；mode='qfq' 用 fore_factor，'hfq' 用 back_factor。
    """
    factor = 1.0
    for f in factors:
        if trade_date >= f['divid_operate_date']:
            factor = f['fore_factor'] if mode == 'qfq' else f['back_factor']
    return price * (factor if factor is not None else 1.0)


# ---------- financial ----------

def upsert_financial_rows(conn, rows: Iterable[tuple]) -> int:
    """批量写财务。rows 每项 (code, year, quarter, pub, stat, roe, gp, np,
    eps, yoy_ni, yoy_asset, liab)。主键冲突整行覆盖（重述以最新为准）。"""
    rows = list(rows)
    if not rows:
        return 0
    before = conn.total_changes
    now = datetime.now().isoformat(timespec='seconds')
    conn.executemany("""
        INSERT INTO financial (code, year, quarter, pub_date, stat_date,
                               roe_avg, gp_margin, np_margin, net_profit,
                               eps_ttm, yoy_ni, yoy_asset, liability_to_asset,
                               updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code, year, quarter) DO UPDATE SET
            pub_date           = excluded.pub_date,
            stat_date          = excluded.stat_date,
            roe_avg            = excluded.roe_avg,
            gp_margin          = excluded.gp_margin,
            np_margin          = excluded.np_margin,
            net_profit         = excluded.net_profit,
            eps_ttm            = excluded.eps_ttm,
            yoy_ni             = excluded.yoy_ni,
            yoy_asset          = excluded.yoy_asset,
            liability_to_asset = excluded.liability_to_asset,
            updated_at         = excluded.updated_at
        """, [r + (now,) for r in rows])
    conn.commit()
    return conn.total_changes - before


def get_max_quarter(conn, code: str) -> Optional[tuple[int, int]]:
    """某股已入库的最新报告期 (year, quarter)；无则 None。"""
    row = conn.execute(
        'SELECT year, quarter FROM financial WHERE code = ?'
        ' ORDER BY year DESC, quarter DESC LIMIT 1',
        (code,),
    ).fetchone()
    return (row['year'], row['quarter']) if row else None


# ---------- index_daily ----------

def upsert_index_daily(conn, rows: Iterable[tuple]) -> int:
    """批量写指数日线。rows 每项与 index_daily 列序一致（code 起 9 列）。"""
    return _insert_ignore(
        conn, 'index_daily',
        '(code, trade_date, open, high, low, close, volume, amount, pct_chg)',
        '(?, ?, ?, ?, ?, ?, ?, ?, ?)',
        rows,
    )


def get_index_max_date(conn, code: str) -> Optional[str]:
    """某指数在 index_daily 中的最新交易日；无则 None。"""
    row = conn.execute(
        'SELECT MAX(trade_date) AS d FROM index_daily WHERE code = ?', (code,)
    ).fetchone()
    return row['d'] if row and row['d'] else None


# ---------- 统计 ----------

def show_stats(conn) -> None:
    """打印各表覆盖情况（行数 + 关键列 min/max），不刷新。"""
    checks = [
        ('leaders', 'code'),
        ('daily', 'trade_date'),
        ('adjust_factor', 'divid_operate_date'),
        ('financial', 'year'),
        ('index_daily', 'trade_date'),
    ]
    print('[refresh] === 各表覆盖情况 ===')
    for table, col in checks:
        n = conn.execute('SELECT COUNT(*) FROM {}'.format(table)).fetchone()[0]
        if n == 0:
            print('  {:<14} 0 行（空表）'.format(table))
            continue
        try:
            lo, hi = conn.execute(
                'SELECT MIN({c}), MAX({c}) FROM {t}'.format(c=col, t=table)
            ).fetchone()
            print('  {:<14} {:>7} 行  {} : {} ~ {}'.format(table, n, col, lo, hi))
        except Exception:
            print('  {:<14} {:>7} 行'.format(table, n))
    # 各股覆盖抽样：找日线行数最少的 5 只（暴露上市晚/拉取失败）
    rows = conn.execute(
        'SELECT code, COUNT(*) AS n, MIN(trade_date) AS lo, MAX(trade_date) AS hi'
        ' FROM daily GROUP BY code ORDER BY n ASC LIMIT 5'
    ).fetchall()
    if rows:
        print('  [daily 最少样本] ' + '; '.join(
            '{}: {}行 {}~{}'.format(r['code'], r['n'], r['lo'], r['hi'])
            for r in rows))
