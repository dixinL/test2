"""个股右侧区间判定主逻辑（四态：左侧 / 震荡 / 弱右侧 / 确认右侧）。

一句话定义：右侧行情 = 下跌趋势终结、底部反转得到验证之后的上涨阶段；
不是最低点，是拐点确认之后。震荡 = 下跌止住但反转未验证的横盘磨底段
（左侧细分，X1~X3 形态条件成立且 P1 未创新低）。仅技术分析思路，不构成投资建议。

价格类指标（均线/MACD/RSI/箱体/摆动低点）一律基于**现算前复权序列**；
量能、状态（is_st/tradestatus/pct_chg）用原始字段。
"""
from __future__ import annotations

import leadersdb

import conditions
import db
import indicators as ind
from name_map import NAME_FIX

WEAK_IDS = ['P1', 'P2', 'M1', 'M2', 'V1', 'E1', 'E2']
STRONG_IDS = ['P1S', 'P2S', 'M1S', 'M2S', 'V1S', 'R1', 'E1S', 'E3']
# 状态起点现算口径：逐日重判与状态列一致的四态；环境快照缺失的历史日
# E1/E2/E1S 为 ❓，视为"环境不否决"（不断开连续性），环境明确 ❌ 正常断开
PRICE_WEAK = ['P1', 'P2', 'M1', 'M2', 'V1']
OSC_IDS = ['X1', 'X2', 'X3']       # 震荡形态条件（左侧细分，见 classify）

# 辅助信号层（仅展示，不参与三态判定；调参需同步 规则.md 第十节）
AUX_DEAD_CROSS_DAYS = 5            # MACD 死叉回看窗口
AUX_OBV_WINDOW = 60                # OBV 顶背离观察窗口
AUX_VOL_WINDOW = 10                # 涨时缩量观察窗口（与 V1S 同口径）
AUX_WIDTH_SOFT_MIN = 40            # 板块结构弱：行业宽度软下限（E1 硬门槛 30 之上留缓冲）
AUX_MARKET_WEAK_STATES = {'弱', '极弱'}   # 大盘结构弱：test1 市场宽度状态
AUX_MARGIN_DROP_DAYS = 5           # 融资退潮：回看交易日数
AUX_MARGIN_DROP = -0.01            # 融资退潮：5 日融资余额变化率下限（≤-1%）


def build_context(conn, code: str, sw2: str, target: str, env_bundle: dict) -> dict:
    """构建单股判定上下文。数据不足时返回 {'status_ok': False}。"""
    env_date, market, industries, trend_date, trends = (
        env_bundle['env_date'], env_bundle['market'], env_bundle['industries'],
        env_bundle['trend_date'], env_bundle['trends'])

    daily = db.load_daily(conn, code, target)
    valid = [r for r in daily if r['close'] is not None]
    if len(valid) < db.MIN_ROWS:
        return {'status_ok': False,
                'status_note': '有效日线 {} 行 < {}'.format(len(valid), db.MIN_ROWS)}
    last = valid[-1]
    if last['trade_date'] != target:
        return {'status_ok': False,
                'status_note': '该股停牌/滞后，最新数据 {}'.format(last['trade_date'])}

    factors = leadersdb.get_adjust_factors(conn, code)

    def qfq(key):
        return [leadersdb.adjusted_price(r[key], r['trade_date'], factors)
                for r in valid]

    close = qfq('close')
    high = qfq('high')
    low = qfq('low')
    i = len(valid) - 1
    dif, dea, _ = ind.macd(close)
    ctx = {
        'dates': [r['trade_date'] for r in valid],
        'close': close, 'high': high, 'low': low,
        'vol': [r['volume'] for r in valid],
        'pct': [r['pct_chg'] for r in valid],
        'ma20': ind.sma(close, 20),
        'ma60': ind.sma(close, 60),
        'dif': dif, 'dea': dea,
        'rsi': ind.rsi(close, 14),
        'obv': ind.obv(close, [r['volume'] for r in valid]),
        'i': i, 'last': last,
    }

    # 板块环境：leaders.sw2 -> env sw_name（含人工修正映射）
    env_name = NAME_FIX.get((sw2 or '').strip(), (sw2 or '').strip())
    env_row = industries.get(env_name)
    if env_date and env_date != target:
        env_note = '(数据截至 {})'.format(env_date)
    elif not env_date:
        env_note = ''
    else:
        env_note = ''
    if env_date and env_row is None:
        env_note = '(行业名对不上: {})'.format(env_name)
    ctx['env_row'] = env_row
    ctx['env_note'] = env_note
    ctx['market'] = env_bundle.get('market')
    ctx['margin'] = env_bundle.get('margin') or []

    # 业绩恶化旗标：最近两个已披露报告期 yoy_ni 均为负
    fins = [f for f in db.load_financial(conn, code)
            if f['pub_date'] and f['pub_date'] <= target and f['yoy_ni'] is not None]
    if len(fins) >= 2 and fins[-1]['yoy_ni'] < 0 and fins[-2]['yoy_ni'] < 0:
        ctx['earnings_note'] = '⚠ yoy_ni 连续两季为负 ({:.1f}%, {:.1f}%)'.format(
            fins[-1]['yoy_ni'], fins[-2]['yoy_ni'])
    else:
        ctx['earnings_note'] = ''

    # 大盘环境
    idx = db.load_index_close(conn, target)
    if len(idx) >= 20:
        closes = [c for _, c in idx]
        ctx['idx_close'] = closes
        ctx['idx_ma20'] = ind.sma(closes, 20)
    else:
        ctx['idx_close'], ctx['idx_ma20'] = None, None

    return ctx


def classify(results: dict) -> str:
    """四态判定：确认右侧 > 弱右侧 > 价格达标(环境缺失) > 震荡 > 左侧。
    F1 未过 -> 排除。震荡仅在弱档"价格"条件未过时判定（横盘磨底），
    板块环境 ❌ 不产生震荡。"""
    if results.get('F1', {}).get('ok') is not True:
        return '排除'
    if any(results[x]['ok'] is not True for x in PRICE_WEAK):
        if (results['P1']['ok'] is not False
                and all(results[x]['ok'] is True for x in OSC_IDS)):
            return '震荡'
        return '左侧'
    if any(results[x]['ok'] is not True for x in ('E1', 'E2')):
        return '左侧'
    if any(results[x]['ok'] is None for x in ('E1', 'E2')):
        return '价格达标(环境缺失)'
    if all(results[x]['ok'] is True for x in STRONG_IDS):
        return '确认右侧'
    return '弱右侧'


def failed_summary(results: dict, state: str) -> str:
    """未过关键条件的摘要（左侧/弱右侧展示用，最多 3 条）。"""
    tier = STRONG_IDS if state == '弱右侧' else WEAK_IDS
    fails = ['{}: {}'.format(conditions.fmt_id(x), results[x]['detail'])
             for x in tier if results.get(x, {}).get('ok') is False]
    return '；'.join(fails[:3])


def aux_warnings(ctx: dict) -> str:
    """辅助信号层（仅展示，不参与三态判定），返回 '·' 连接的预警串或 ''。

    个股量价类：
    - MACD死叉：近 AUX_DEAD_CROSS_DAYS 日 DIF 下穿 DEA（趋势动能转弱预警）
    - OBV背离：近 AUX_OBV_WINDOW 日收盘创新高而 OBV 未同步新高（量价背离）
    - 涨时缩量：近 AUX_VOL_WINDOW 日 上涨日均量 < 下跌日均量（与 V1S 同口径）
    市场结构类（test1 env 快照，缺失时静默跳过）：
    - 板块弱：行业宽度 < AUX_WIDTH_SOFT_MIN（宽度=行业内 MA20 上方个股占比，
      在 E1 硬门槛 30 之上预警结构单薄）
    - 大盘弱：市场宽度状态 ∈ AUX_MARKET_WEAK_STATES（test1 市场判断）
    杠杆资金类（leaders.db margin 表，沪深合计融资余额，缺失时静默跳过）：
    - 融资退潮：近 AUX_MARGIN_DROP_DAYS 日融资余额变化率 ≤ AUX_MARGIN_DROP
    """
    i = ctx['i']
    warn = []
    dif, dea = ctx['dif'], ctx['dea']
    for d in range(max(1, i - AUX_DEAD_CROSS_DAYS + 1), i + 1):
        if dif[d - 1] >= dea[d - 1] and dif[d] < dea[d]:
            warn.append('MACD死叉')
            break
    if i >= AUX_OBV_WINDOW - 1:
        w = AUX_OBV_WINDOW
        closes = ctx['close'][i - w + 1:i + 1]
        obvs = ctx['obv'][i - w + 1:i + 1]
        if closes[-1] >= max(closes) and obvs[-1] < max(obvs):
            warn.append('OBV背离')
    if i >= AUX_VOL_WINDOW:
        pct, vol = ctx['pct'], ctx['vol']
        up_v = [vol[k] for k in range(i - AUX_VOL_WINDOW + 1, i + 1)
                if pct[k] is not None and pct[k] > 0 and vol[k]]
        dn_v = [vol[k] for k in range(i - AUX_VOL_WINDOW + 1, i + 1)
                if pct[k] is not None and pct[k] < 0 and vol[k]]
        if up_v and dn_v and sum(up_v) / len(up_v) < sum(dn_v) / len(dn_v):
            warn.append('涨时缩量')
    env = ctx.get('env_row')
    if env:
        try:
            if float(env.get('width')) < AUX_WIDTH_SOFT_MIN:
                warn.append('板块弱')
        except (TypeError, ValueError):
            pass
    market = ctx.get('market')
    if market and market.get('width_state') in AUX_MARKET_WEAK_STATES:
        warn.append('大盘弱')
    margin = ctx.get('margin') or []
    if len(margin) > AUX_MARGIN_DROP_DAYS:
        base = margin[-1 - AUX_MARGIN_DROP_DAYS][1]
        if base and margin[-1][1] is not None \
                and margin[-1][1] / base - 1 <= AUX_MARGIN_DROP:
            warn.append('融资退潮')
    return '·'.join(warn)


def fmt_margin(margin: list[tuple[str, float]]) -> str:
    """融资余额温度计展示行：'融资余额 23779.02 亿（5日 -0.9%）'。

    数据不足 6 个交易日时返回空串（不展示）。"""
    if margin and len(margin) > AUX_MARGIN_DROP_DAYS:
        base = margin[-1 - AUX_MARGIN_DROP_DAYS][1]
        if base:
            chg = margin[-1][1] / base - 1
            return '融资余额 {:.0f} 亿（{}日 {:+.1%}）'.format(
                margin[-1][1], AUX_MARGIN_DROP_DAYS, chg)
    return ''


def _walk_state(results: dict) -> str:
    """历史日重判三态（与状态列同口径）。环境 ❓ 不否决：
    E1/E2 明确 ❌ → 左侧；❓ 跳过；价格全过 + 强档（含可用的 E1S/E3）全过
    → 确认右侧，否则弱右侧。"""
    for x in PRICE_WEAK:
        if results[x]['ok'] is False:
            if (results['P1']['ok'] is not False
                    and all(results[o]['ok'] is True for o in OSC_IDS)):
                return '震荡'
            return '左侧'
    if results['E1']['ok'] is False or results['E2']['ok'] is False:
        return '左侧'
    if all(results[x]['ok'] is True for x in STRONG_IDS):
        return '确认右侧'
    return '弱右侧'


def state_start_real(ctx: dict, state: str):
    """从基础数据现算状态起点：在完整日线上逐日重判四态（与状态列同
    口径），取当前连续段起点。返回 (start_date, days, bounded)；
    bounded=True 表示回溯到可评估序列开头仍未翻转（真实起点更早）。"""
    dates = ctx['dates']
    first = conditions.LOW_WINDOW - 1   # 首个可评估索引
    i = ctx['i']
    cls = '弱右侧' if state == '价格达标(环境缺失)' else state
    start, days = i, 1
    j = i - 1
    while j >= first:
        if _walk_state(conditions.evaluate({**ctx, 'i': j})) != cls:
            break
        start, days = j, days + 1
        j -= 1
    return dates[start], days, start == first


def fmt_start(start_date: str, days: int, bounded: bool) -> str:
    """状态起点展示：贯穿可评估序列时显示完整日期的"早于"（可能跨多年），
    否则 MM-DD 起（N天）。"""
    if bounded:
        return '早于 {}'.format(start_date)
    return '{} 起（{}天）'.format(start_date[5:], days)
