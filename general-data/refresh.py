"""general-data 基础数据统一刷新入口（baostock 版）。

把 4 张表（leaders / daily / adjust_factor / financial）的刷新集中在一个入口：

用法:
    python refresh.py                        # 日常增量（daily+因子+近两季财报）
    python refresh.py --full                 # 首灌：daily 从 2020 年起，财报从 2020Q1 起
    python refresh.py --no-financial         # 跳过财报（行情快速刷新）
    python refresh.py --financial-since 2022Q1  # 财报回填起点
    python refresh.py --daily-since 2026-09-15 # 日线回填起点（近1个月补缺）
    python refresh.py --limit 3              # 只处理前 N 只（调试/小样本验证）
    python refresh.py --stats                # 只打印各表覆盖情况，不刷新

写入全部幂等：daily/adjust_factor 历史行永不重写（存不复权价，除权只进因子
表）；financial 主键冲突覆盖（重述以最新为准）。重复运行"新增 0 行"即就绪。
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date

import baostock as bs

import bsfetch
import leadersdb

FULL_START = '2020-01-01'          # daily 首灌起点
FULL_FIN_YEAR, FULL_FIN_QUARTER = 2020, 1   # 财报首灌起点
INDEX_CODE = 'sh.000001'           # 上证综指（大盘环境判定用）


def current_quarter(today: date) -> tuple[int, int]:
    """今天所处的报告期 (year, quarter)。"""
    return today.year, (today.month - 1) // 3 + 1


def next_quarter(y: int, q: int) -> tuple[int, int]:
    return (y + 1, 1) if q == 4 else (y, q + 1)


def refresh_leaders(conn) -> int:
    """龙头维表：CSV 全量 upsert。返回行数。"""
    n = leadersdb.import_leaders_from_csv(conn)
    print('[refresh] leaders: {} 只（CSV 同步完成）'.format(n))
    return n


def refresh_daily(conn, leaders, since=None) -> dict:
    """逐股增量拉日线：从库内 max_date+1（空则 2020-01-01）拉到今天。
    since 给定时强制回填起点（幂等补缺，已有行不重写）。
    返回 {'ok': n, 'failed': n, 'inserted': n}。"""
    today = date.today().isoformat()
    ok = failed = inserted = 0
    for it in leaders:
        code = it['code']
        max_d = leadersdb.get_max_trade_date(conn, code)
        if since:
            start, mode = since, '回填'
        elif max_d:
            y, m, d = (int(x) for x in max_d.split('-'))
            start = date.fromordinal(date(y, m, d).toordinal() + 1).isoformat()
            mode = '增量'
        else:
            start, mode = FULL_START, '首灌'
        try:
            rows = bsfetch.fetch_daily(code, it['exchange'], start, today)
            n = leadersdb.upsert_daily_rows(conn, rows)
            inserted += n
            ok += 1
            print('  [{}] {} {}: {} 行, 新增 {}'.format(
                mode, code, it['name'], len(rows), n))
        except Exception as err:
            failed += 1
            print('  [WARN] {} {}: {}'.format(code, it['name'], err))
    print('[refresh] daily: 成功 {} · 失败 {} · 新增 {} 行'.format(
        ok, failed, inserted))
    return {'ok': ok, 'failed': failed, 'inserted': inserted}


def refresh_adjust_factors(conn, leaders) -> int:
    """逐股拉全量复权因子（每股十几条，代价小），INSERT OR IGNORE。"""
    inserted = 0
    for it in leaders:
        try:
            rows = bsfetch.fetch_adjust_factor(it['code'], it['exchange'])
            inserted += leadersdb.upsert_adjust_factors(conn, rows)
        except Exception as err:
            print('  [WARN] 因子 {} {}: {}'.format(it['code'], it['name'], err))
    print('[refresh] adjust_factor: 新增 {} 行'.format(inserted))
    return inserted


def refresh_financial(conn, leaders, since=None, recent_quarters=2) -> dict:
    """逐股逐季拉财务，UPSERT。

    起点 = since / 库内 max_quarter 的下一季 / 近 recent_quarters 季（空库时）。
    返回 {'ok': n, 'failed': n, 'upserted': n}。
    """
    cy, cq = current_quarter(date.today())
    ok = failed = upserted = 0
    for it in leaders:
        code = it['code']
        max_q = leadersdb.get_max_quarter(conn, code)
        if since:
            y, q = since
        elif max_q:
            y, q = next_quarter(*max_q)
        else:
            # 空库：近 N 季（含当前报告期，未发布则空结果跳过）
            idx = (cy - 1) * 4 + cq - recent_quarters
            y, q = (idx - 1) // 4 + 1, (idx - 1) % 4 + 1
        if max_q and (y, q) > (cy, cq):
            continue  # 已是最新
        while (y, q) <= (cy, cq):
            try:
                row = bsfetch.fetch_financial(code, it['exchange'], y, q)
                if row is not None:
                    upserted += leadersdb.upsert_financial_rows(conn, [row])
                ok += 1
            except Exception as err:
                failed += 1
                print('  [WARN] 财务 {} {}Q{}: {}'.format(code, y, q, err))
            y, q = next_quarter(y, q)
    print('[refresh] financial: 查询 {} 季 · 失败 {} · 新增/更新 {} 行'.format(
        ok, failed, upserted))
    return {'ok': ok, 'failed': failed, 'upserted': upserted}


def refresh_index(conn) -> int:
    """上证综指日线增量刷新：从库内 max_date+1 拉到今天，INSERT OR IGNORE。
    返回新增行数。"""
    today = date.today().isoformat()
    max_d = leadersdb.get_index_max_date(conn, INDEX_CODE)
    if max_d:
        y, m, d = (int(x) for x in max_d.split('-'))
        start = date.fromordinal(date(y, m, d).toordinal() + 1).isoformat()
        if start > today:
            print('[refresh] index {}: 已是最新 ({})'.format(INDEX_CODE, max_d))
            return 0
    else:
        start = FULL_START
    rows = bsfetch.fetch_index_daily(INDEX_CODE, start, today)
    n = leadersdb.upsert_index_daily(conn, rows)
    print('[refresh] index_daily {}: 新增 {} 行 ({}~)'.format(
        INDEX_CODE, n, start))
    return n


def parse_quarter(s: str) -> tuple[int, int]:
    """'2022Q1'/'2022q1' -> (2022, 1)。"""
    s = s.strip().upper()
    year, q = s.split('Q')
    return int(year), int(q)


def show_stats(conn) -> int:
    leadersdb.show_stats(conn)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='general-data 基础数据统一刷新')
    parser.add_argument('--full', action='store_true',
                        help='首灌：财务从 2020Q1 起（daily 本就从 2020-01-01 起）')
    parser.add_argument('--no-financial', action='store_true',
                        help='跳过财报表（行情快速刷新）')
    parser.add_argument('--financial-since', type=str, default=None,
                        help='财报回填起点，格式 2020Q1')
    parser.add_argument('--daily-since', type=str, default=None,
                        help='日线回填起点 YYYY-MM-DD（如近1个月：从该日强制拉到今天，幂等补缺）')
    parser.add_argument('--limit', type=int, default=None,
                        help='只处理前 N 只股票（调试用）')
    parser.add_argument('--stats', action='store_true',
                        help='只打印各表覆盖情况，不刷新')
    args = parser.parse_args()

    t0 = time.time()
    conn = leadersdb.connect()
    leadersdb.init_schema(conn)

    bs.login()  # 忽略输出；_query 内部有失败重登兜底

    if args.stats:
        rc = show_stats(conn)
        conn.close()
        return rc

    refresh_leaders(conn)
    all_leaders = leadersdb.get_all_leaders(conn)
    leaders = [r for r in all_leaders if r['exchange'] in ('SH', 'SZ')]
    n_skip = len(all_leaders) - len(leaders)
    if n_skip:
        print('[refresh] 跳过 {} 只非 A 股（港股/美股，baostock 不覆盖，维表已保留）'.format(n_skip))
    if args.limit:
        leaders = leaders[:args.limit]
    if not leaders:
        print('[refresh] [ERROR] 龙头维表为空，无法继续')
        return 1

    refresh_daily(conn, leaders, since=args.daily_since)
    refresh_adjust_factors(conn, leaders)

    if not args.no_financial:
        since = None
        if args.financial_since:
            since = parse_quarter(args.financial_since)
        elif args.full:
            since = (FULL_FIN_YEAR, FULL_FIN_QUARTER)
        refresh_financial(conn, leaders, since=since)

    refresh_index(conn)

    conn.close()
    bs.logout()
    print('[refresh] 完成，用时 {:.1f}s'.format(time.time() - t0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
