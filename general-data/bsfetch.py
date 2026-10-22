"""general-data 数据获取层（baostock 唯一数据抓取入口）。

职责：调用 baostock 拉取数据并标准化，写库复用 leadersdb。
数据源：
- 个股日线（不复权 + 估值 + 状态）: query_history_k_data_plus, adjustflag='3'
- 复权因子:                          query_adjust_factor
- 季频财务:                          query_profit_data / query_growth_data /
                                     query_balance_data
"""
from __future__ import annotations

import time
from datetime import date
from typing import Optional

import leadersdb

try:
    import baostock as bs
except ImportError as exc:
    raise SystemExit('缺少依赖 baostock，请先 pip install baostock') from exc

FETCH_MAX_RETRY = 3
FETCH_RETRY_BACKOFF = 2.0

DAILY_FIELDS = (
    'date,code,open,high,low,close,preclose,volume,amount,'
    'turn,tradestatus,pctChg,isST,peTTM,pbMRQ,psTTM,pcfNcfTTM'
)


def to_bs_code(code6: str, exchange: str) -> str:
    """6位代码 + 交易所 -> baostock 代码格式，如 ('000998','SZ') -> 'sz.000998'。"""
    return '{}.{}'.format(str(exchange).lower(), str(code6))


def _num(v):
    """baostock 空串转 None，其余转 float。"""
    if v is None or str(v).strip() == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _query(fn, *args, **kwargs):
    """带重试的 baostock 查询：失败退避后重登再试。返回 (rows, fields)。"""
    last_err = None
    for attempt in range(1, FETCH_MAX_RETRY + 1):
        try:
            rs = fn(*args, **kwargs)
            if rs.error_code != '0':
                raise RuntimeError('baostock 错误 {}: {}'.format(
                    rs.error_code, rs.error_msg))
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            return rows, rs.fields
        except Exception as err:
            last_err = err
            if attempt < FETCH_MAX_RETRY:
                time.sleep(FETCH_RETRY_BACKOFF * attempt)
                bs.login()
    raise RuntimeError('baostock 查询失败（重试 {} 次）: {}'.format(
        FETCH_MAX_RETRY, last_err))


def fetch_daily(code6: str, exchange: str, start: str, end: str) -> list[tuple]:
    """拉个股日线（不复权，含估值/状态），返回与 daily 表列序一致的 tuple 列表。

    start/end 格式 YYYY-MM-DD。停牌日行保留（tradestatus=0，价格为空）。
    """
    rows, _ = _query(
        bs.query_history_k_data_plus,
        to_bs_code(code6, exchange), DAILY_FIELDS,
        start_date=start, end_date=end, frequency='d', adjustflag='3',
    )
    out = []
    for r in rows:
        # r: date, code, open, high, low, close, preclose, volume, amount,
        #    turn, tradestatus, pctChg, isST, peTTM, pbMRQ, psTTM, pcfNcfTTM
        out.append((
            code6, r[0],
            _num(r[2]), _num(r[3]), _num(r[4]), _num(r[5]), _num(r[6]),
            _num(r[7]), _num(r[8]), _num(r[9]), _num(r[11]),
            int(float(r[10])) if r[10] else None,
            int(float(r[12])) if r[12] else None,
            _num(r[13]), _num(r[14]), _num(r[15]), _num(r[16]),
        ))
    return out


def fetch_adjust_factor(code6: str, exchange: str) -> list[tuple]:
    """拉个股全部复权因子，返回 (code, divid_operate_date, fore, back, adj)。"""
    rows, _ = _query(bs.query_adjust_factor, code=to_bs_code(code6, exchange))
    return [
        (code6, r[1], _num(r[2]), _num(r[3]), _num(r[4]))
        for r in rows
    ]


def fetch_index_daily(bs_code: str = 'sh.000001', start: str = '2020-01-01',
                      end: str | None = None) -> list[tuple]:
    """拉指数日线（不复权，指数无复权概念），返回与 index_daily 表列序一致的
    tuple 列表 (code, trade_date, open, high, low, close, volume, amount, pct_chg)。

    bs_code 形如 'sh.000001'（上证综指）。"""
    end = end or date.today().isoformat()
    rows, _ = _query(
        bs.query_history_k_data_plus, bs_code,
        'date,code,open,high,low,close,volume,amount,pctChg',
        start_date=start, end_date=end, frequency='d',
    )
    out = []
    for r in rows:
        # r: date, code, open, high, low, close, volume, amount, pctChg
        out.append((
            bs_code, r[0],
            _num(r[2]), _num(r[3]), _num(r[4]), _num(r[5]),
            _num(r[6]), _num(r[7]), _num(r[8]),
        ))
    return out


def fetch_financial(code6: str, exchange: str, year: int, quarter: int) -> Optional[tuple]:
    """拉某报告期财务（profit+growth+balance 三接口合并）。

    返回 (code, year, quarter, pub_date, stat_date, roe_avg, gp_margin,
          np_margin, net_profit, eps_ttm, yoy_ni, yoy_asset, liability_to_asset)
    该报告期无数据（未发布）返回 None。
    """
    # 三接口字段都含 code/pubDate/statDate，逐个转 dict 合并
    def to_dicts(rows, fields):
        return [dict(zip(fields, r)) for r in rows]

    p_rows, p_fields = _query(
        bs.query_profit_data, code=to_bs_code(code6, exchange),
        year=year, quarter=quarter)
    g_rows, g_fields = _query(
        bs.query_growth_data, code=to_bs_code(code6, exchange),
        year=year, quarter=quarter)
    b_rows, b_fields = _query(
        bs.query_balance_data, code=to_bs_code(code6, exchange),
        year=year, quarter=quarter)
    if not p_rows:
        return None
    p = to_dicts(p_rows, p_fields)[0]
    g = to_dicts(g_rows, g_fields)[0] if g_rows else {}
    b = to_dicts(b_rows, b_fields)[0] if b_rows else {}
    return (
        code6, year, quarter,
        p.get('pubDate') or None,
        p.get('statDate') or None,
        _num(p.get('roeAvg')),
        _num(p.get('gpMargin')),
        _num(p.get('npMargin')),
        _num(p.get('netProfit')),
        _num(p.get('epsTTM')),
        _num(g.get('YOYNI')),
        _num(g.get('YOYAsset')),
        _num(b.get('liabilityToAsset')),
    )
