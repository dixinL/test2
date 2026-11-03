"""融资融券大盘汇总抓取（akshare 交易所接口，T+1 发布）。

数据源（沪深两所官方汇总口径）:
- ak.stock_margin_sse(start_date, end_date)  上交所逐日汇总，融资余额单位: 元
- ak.stock_margin_szse(date)                 深交所单日汇总，单位: 亿元

写入 leaders.db margin 表（沪深合计，统一亿元）：trade_date 主键，
INSERT OR IGNORE 幂等；缺失日（接口偶发失败/T+1 未发布）后续运行自动补齐。

用法（一般经 refresh.py 调度，也可独立运行）:
    python marginfetch.py                    # 增量：库内最新+1 起到今天
    python marginfetch.py --since 20261001   # 指定回填起点 (YYYYMMDD)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta

try:
    import akshare as ak
except ImportError:  # CI 环境可选依赖，缺 akshare 时静默跳过
    ak = None

import leadersdb

DEFAULT_BACKFILL_DAYS = 120      # 空表默认回填自然日数（约 80 个交易日）
SZSE_SLEEP = 0.5                 # 深交所单日接口限速间隔（秒）


def _to_yi(v) -> float | None:
    """接口值转亿元 float（空串/非数值转 None）。"""
    if v is None or str(v).strip() == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_and_store_margin(conn, since: str | None = None) -> int:
    """增量抓取沪深两融汇总并写库，返回新增行数。

    起点 = since / 库内最新+1 自然日 / 空表默认近 DEFAULT_BACKFILL_DAYS 天。
    深交所按日逐个请求（仅拉缺行日期），单日失败跳过待后续补齐。
    """
    if ak is None:
        print('[margin] [WARN] 未安装 akshare，跳过两融刷新')
        return 0
    today = date.today()
    if since:
        start = date(int(since[:4]), int(since[4:6]), int(since[6:8]))
    else:
        max_d = leadersdb.get_margin_max_date(conn)
        if max_d:
            y, m, d = (int(x) for x in max_d.split('-'))
            start = date.fromordinal(date(y, m, d).toordinal() + 1)
        else:
            start = today - timedelta(days=DEFAULT_BACKFILL_DAYS)
    if start > today:
        print('[margin] 已是最新 ({})'.format(leadersdb.get_margin_max_date(conn)))
        return 0

    # 上交所：区间一次拉齐（单位元 -> 亿元），同时给出交易日日历
    try:
        sse = ak.stock_margin_sse(
            start_date=start.strftime('%Y%m%d'), end_date=today.strftime('%Y%m%d'))
    except Exception as err:
        print('[margin] [WARN] SSE 拉取失败（可能区间无新数据，待下次补齐）: {}'.format(err))
        return 0
    if sse is None or sse.empty:
        print('[margin] SSE 区间无数据 ({}~{})'.format(start, today))
        return 0

    have = {r['trade_date'] for r in conn.execute(
        'SELECT trade_date FROM margin WHERE trade_date >= ?',
        (start.isoformat(),))}
    rows, sz_miss = [], 0
    for _, r in sse.iterrows():
        d = str(r['信用交易日期'])[:10]
        d = '{}-{}-{}'.format(d[:4], d[4:6], d[6:8])  # 20261029 -> 2026-10-29
        if d in have:
            continue
        rzye = _to_yi(r['融资余额'])
        if rzye is None:
            continue
        rzye /= 1e8
        rqye = _to_yi(r['融券余量金额'])
        rqye = rqye / 1e8 if rqye is not None else None
        # 深交所：单日汇总（单位亿元），失败日跳过留待补齐
        try:
            sz = ak.stock_margin_szse(date=d.replace('-', ''))
            sz_rzye = _to_yi(sz['融资余额'].sum()) if sz is not None and not sz.empty else None
        except Exception:
            sz_rzye = None
        if sz_rzye is None:
            sz_miss += 1
            continue
        sz_rqye = (_to_yi(sz['融券余额'].sum())
                   if '融券余额' in sz.columns else None) or 0
        rows.append((d, round(rzye + sz_rzye, 2),
                     round((rqye or 0) + sz_rqye, 2)))
        time.sleep(SZSE_SLEEP)
    n = leadersdb.upsert_margin_rows(conn, rows)
    print('[margin] 区间 {}~{}: 沪深合计新增 {} 行{}'.format(
        start, today, n, ' · 深市缺失 {} 日(待补齐)'.format(sz_miss) if sz_miss else ''))
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description='融资融券大盘汇总抓取')
    parser.add_argument('--since', type=str, default=None,
                        help='回填起点 YYYYMMDD（空表默认近 {} 天）'.format(
                            DEFAULT_BACKFILL_DAYS))
    args = parser.parse_args()

    conn = leadersdb.connect()
    try:
        leadersdb.init_schema(conn)
        n = fetch_and_store_margin(conn, since=args.since)
        return 0 if n >= 0 else 1
    finally:
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
