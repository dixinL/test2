"""右侧判定条件组定义（每条输出 通过与否 + 可追溯明细值）。

条件编号与定稿方案一致（首版默认参数，调参只改本文件常量）：
  P1 不再创新低 / P2 箱体与突破 / M1 均线 / M2 MACD / V1 量能 / R1 RSI
  E1 板块环境 / E2 拥挤度过滤 / E3 大盘环境 / F1 排雷
弱右侧/确认右侧的适用档位见各条件的 tier 说明。
"""
from __future__ import annotations

import indicators as ind

# ---- 可调参数（定稿方案首版默认值）----
LOW_WINDOW = 120          # P1: 历史新低窗口
NO_NEW_LOW_DAYS = 20      # P1: 近 N 日未创新低
BOX_WINDOW = 60           # P2: 箱体窗口
BREAK_STAND_DAYS = 10     # P2弱/V1弱: 突破回看窗口（突破后站稳即可，不必当日）
PULLBACK_DAYS = 20        # P2强: 突破回看窗口
VOL_BREAK_RATIO = 1.5     # V1弱: 突破日量 / 箱体期均量
VOL_HEALTHY_RATIO = 1.2   # V1强: 近 20 日上涨日均量 / 下跌日均量
VOL_HEALTHY_DAYS = 20
MA60_ABOVE_DAYS = 5       # M1强: 收盘站上 MA60 连续 N 日
MACD_CROSS_DAYS = 30      # M2弱: MACD 金叉回看窗口（不限 0 轴位置）
SWING_LOW_DAYS = 20       # P1强: 摆动低点抬升回看窗口
E1_WIDTH_MIN = 30         # E1弱: 板块宽度下限
E2_CONG_MAX = 80          # E2: 板块拥挤度上限
E1_CONFIRM_SCENES = {'S2', 'S4', 'S8'}   # E1强: 积极场景集


def _c(v):
    """bool/None -> 输出符号。"""
    return {True: '✅', False: '❌', None: '❓'}.get(v, '❓')


def evaluate(ctx: dict) -> dict[str, dict]:
    """评估全部条件。ctx 由 rightside.build_context 构建的数据不足时该键为 None。"""
    r: dict[str, dict] = {}

    # ---------- F1 排雷（前置门槛） ----------
    if ctx.get('status_ok') is False:
        return {'F1': {'ok': False, 'detail': ctx.get('status_note', '数据不足'),
                       'tier': 'gate'}}
    last = ctx['last']
    st_ok = (last['is_st'] in (0, None)) and (last['tradestatus'] in (1, None))
    r['F1'] = {'ok': st_ok,
               'detail': 'is_st={} tradestatus={}'.format(
                   last['is_st'], last['tradestatus']),
               'tier': 'gate'}
    # 业绩恶化旗标（确认右侧附加提示，不硬性拦截）
    r['F1F'] = {'ok': None, 'tier': 'flag', 'detail': ctx.get('earnings_note', '')}

    # ---------- P1 不再创新低 ----------
    low = ctx['low']
    i = ctx['i']
    if i + 1 < LOW_WINDOW:
        r['P1'] = r['P1S'] = {'ok': None, 'detail': '上市不足 {} 个交易日'.format(LOW_WINDOW),
                              'tier': 'weak'}
    else:
        min20 = min(low[i - NO_NEW_LOW_DAYS + 1:i + 1])
        min120 = min(low[i - LOW_WINDOW + 1:i + 1])
        ok = min120 < min20
        r['P1'] = {'ok': ok,
                   'detail': '近{}日低 {:.2f} vs {}日低 {:.2f}'.format(
                       NO_NEW_LOW_DAYS, min20, LOW_WINDOW, min120),
                   'tier': 'weak'}
        # 强档：近 20 日摆动低点逐级抬升
        lows_idx = [k for k in ind.swing_lows(low) if i - SWING_LOW_DAYS < k <= i]
        vals = [low[k] for k in lows_idx]
        ok_s = len(vals) >= 2 and all(
            vals[j] > vals[j - 1] for j in range(1, len(vals)))
        r['P1S'] = {'ok': ok_s,
                    'detail': '近{}日摆动低点 {}: {}'.format(
                        SWING_LOW_DAYS, len(vals),
                        ', '.join('{:.2f}'.format(v) for v in vals) or '无'),
                    'tier': 'strong'}
    # ---------- P2 箱体突破（近 N 日突破且站稳）/ V1 突破日量能 / P2S 回踩不破 ----------
    high, close, vol = ctx['high'], ctx['close'], ctx['vol']
    if i < BOX_WINDOW:
        r['P2'] = r['P2S'] = {'ok': None, 'detail': '不足一个箱体窗口',
                              'tier': 'weak'}
    else:
        # 弱档：近 BREAK_STAND_DAYS 日内存在突破日 b（收盘>其前 BOX_WINDOW 上沿），
        # 且 b 起收盘从未收于该上沿之下（站稳口径，不要求当日即为突破日）；
        # 取最近一个站稳的突破日
        hit = None
        for b in range(max(i - BREAK_STAND_DAYS, BOX_WINDOW), i + 1):
            top_b = max(high[b - BOX_WINDOW:b])
            if close[b] > top_b and min(close[b:i + 1]) >= top_b:
                hit = (b, top_b)
        if hit:
            b, top_b = hit
            r['P2'] = {'ok': True,
                       'detail': '突破日 {}，现收 {:.2f} ≥ 上沿 {:.2f}({}日)，站稳'.format(
                           ctx['dates'][b], close[i], top_b, BOX_WINDOW),
                       'tier': 'weak'}
            # V1弱：该突破日量能 ≥ VOL_BREAK_RATIO × 其前箱体期均量
            if all(v is not None for v in vol[b - BOX_WINDOW:b]) and vol[b]:
                box_vol_avg = sum(vol[b - BOX_WINDOW:b]) / BOX_WINDOW
                ratio = vol[b] / box_vol_avg if box_vol_avg else 0
                r['V1'] = {'ok': ratio >= VOL_BREAK_RATIO,
                           'detail': '突破日量/箱体均量 = {:.2f} (≥{:.1f})'.format(
                               ratio, VOL_BREAK_RATIO), 'tier': 'weak'}
            else:
                r['V1'] = {'ok': None, 'detail': '箱体期量能缺失', 'tier': 'weak'}
        else:
            r['P2'] = {'ok': False,
                       'detail': '近{}日无站稳突破'.format(BREAK_STAND_DAYS),
                       'tier': 'weak'}
            r['V1'] = {'ok': False,
                       'detail': '近{}日无有效突破'.format(BREAK_STAND_DAYS),
                       'tier': 'weak'}
        # 强档：突破后回踩不破上沿（近 PULLBACK_DAYS 内出现突破，此后低点不破）
        ok_pb, pb_info = False, '近{}日无有效突破'.format(PULLBACK_DAYS)
        for b in range(max(i - PULLBACK_DAYS, BOX_WINDOW), i):
            top_b = max(high[b - BOX_WINDOW:b])
            if close[b] > top_b and min(low[b + 1:i + 1]) >= top_b \
                    and close[i] >= top_b:
                ok_pb = True
                pb_info = '突破日 {} 收 {:.2f} 上沿 {:.2f}，回踩低点低至 {:.2f}'.format(
                    ctx['dates'][b], close[b], top_b, min(low[b + 1:i + 1]))
                break
        r['P2S'] = {'ok': ok_pb, 'detail': pb_info, 'tier': 'strong'}

    # ---------- M1 均线 ----------
    ma20, ma60 = ctx['ma20'], ctx['ma60']
    if ma20[i] is None:
        r['M1'] = {'ok': None, 'detail': 'MA20 未就绪', 'tier': 'weak'}
    else:
        slope_ok = ma20[i] >= ma20[i - 1] if i >= 1 else False
        r['M1'] = {'ok': close[i] > ma20[i] and slope_ok,
                   'detail': '收盘 {:.2f} vs MA20 {:.2f}，MA20 斜率 {}'.format(
                       close[i], ma20[i], '↑' if slope_ok else '↓'),
                   'tier': 'weak'}
    if ma60[i] is None:
        r['M1S'] = {'ok': None, 'detail': 'MA60 未就绪', 'tier': 'strong'}
    else:
        above_days = 0
        k = i
        while k >= 0 and ma60[k] is not None and close[k] > ma60[k]:
            above_days += 1
            k -= 1
        states = [ind.ma_state(ma60, j) for j in range(max(0, i - 29), i + 1)]
        pattern = ind.has_pattern_down_flat_up(states)
        ok_s = pattern and above_days >= MA60_ABOVE_DAYS
        r['M1S'] = {'ok': ok_s,
                    'detail': '60日线三态{}，站上MA60连续{}日(≥{})'.format(
                        '已现 下→平→上' if pattern else '未现', above_days,
                        MA60_ABOVE_DAYS),
                    'tier': 'strong'}

    # ---------- M2 MACD 金叉（不限 0 轴位置） ----------
    dif, dea = ctx['dif'], ctx['dea']
    cross = False
    for d in range(max(1, i - MACD_CROSS_DAYS + 1), i + 1):
        if dif[d - 1] <= dea[d - 1] and dif[d] > dea[d]:
            cross = True
            break
    r['M2'] = {'ok': cross,
               'detail': '近{}日MACD金叉: {}'.format(MACD_CROSS_DAYS, cross),
               'tier': 'weak'}
    r['M2S'] = {'ok': dif[i] > 0,
                'detail': 'DIF {:.4f} ({})'.format(dif[i], '0轴上' if dif[i] > 0 else '0轴下'),
                'tier': 'strong'}

    # ---------- V1强 量价健康（涨跌方向用 baostock pct_chg，除权日口径正确） ----------
    if i >= VOL_HEALTHY_DAYS:
        pct = ctx['pct']
        up_v = [vol[k] for k in range(i - VOL_HEALTHY_DAYS + 1, i + 1)
                if pct[k] is not None and pct[k] > 0 and vol[k]]
        dn_v = [vol[k] for k in range(i - VOL_HEALTHY_DAYS + 1, i + 1)
                if pct[k] is not None and pct[k] < 0 and vol[k]]
        if up_v and dn_v:
            ratio = (sum(up_v) / len(up_v)) / (sum(dn_v) / len(dn_v))
            r['V1S'] = {'ok': ratio >= VOL_HEALTHY_RATIO,
                        'detail': '近{}日 上涨日均量/下跌日均量 = {:.2f} (≥{:.1f})'.format(
                            VOL_HEALTHY_DAYS, ratio, VOL_HEALTHY_RATIO),
                        'tier': 'strong'}
        else:
            r['V1S'] = {'ok': None, 'detail': '上涨/下跌样本不足', 'tier': 'strong'}
    else:
        r['V1S'] = {'ok': None, 'detail': '数据不足', 'tier': 'strong'}

    # ---------- R1 RSI ----------
    rsi_v = ctx['rsi'][i]
    r['R1'] = {'ok': (rsi_v >= 50) if rsi_v is not None else None,
               'detail': 'RSI14 = {:.1f}'.format(rsi_v) if rsi_v is not None else '未就绪',
               'tier': 'strong'}

    # ---------- E1/E2 板块环境（env 数据缺失时 None） ----------
    env = ctx.get('env_row')
    env_note = ctx.get('env_note', '')
    if env:
        try:
            width_v = float(env['width'])
        except (TypeError, ValueError):
            width_v = None
        try:
            cong_v = float(env['congestion'])
        except (TypeError, ValueError):
            cong_v = None
        scene = env.get('scenario', '')
        signal = env.get('signal', '')
        r['E1'] = {'ok': (width_v >= E1_WIDTH_MIN) if width_v is not None else None,
                   'detail': '宽度 {} (≥{}) [{}]'.format(env.get('width'), E1_WIDTH_MIN, env_note),
                   'tier': 'weak'}
        r['E1S'] = {'ok': scene in E1_CONFIRM_SCENES or signal == '★',
                    'detail': '场景 {} 信号 {} [{}]'.format(scene, signal, env_note),
                    'tier': 'strong'}
        r['E2'] = {'ok': (cong_v < E2_CONG_MAX) if cong_v is not None else None,
                   'detail': '拥挤度 {} (<{}) [{}]'.format(env.get('congestion'), E2_CONG_MAX, env_note),
                   'tier': 'weak'}
    else:
        r['E1'] = r['E1S'] = r['E2'] = {
            'ok': None, 'detail': '环境快照缺失' + env_note, 'tier': 'weak'}

    # ---------- E3 大盘环境 ----------
    idx_close, idx_ma20, idx_ma20_val = ctx.get('idx_close'), ctx.get('idx_ma20'), None
    if idx_close and idx_ma20 and idx_ma20[-1] is not None:
        r['E3'] = {'ok': idx_close[-1] > idx_ma20[-1],
                   'detail': '上证 {:.2f} vs MA20 {:.2f}'.format(
                       idx_close[-1], idx_ma20[-1]),
                   'tier': 'strong'}
    else:
        r['E3'] = {'ok': None, 'detail': '指数数据不足', 'tier': 'strong'}

    return r


def fmt_id(cid: str) -> str:
    """条件编号 -> 展示名。"""
    names = {
        'P1': '不再创新低', 'P1S': '低点抬升', 'P2': '箱体突破', 'P2S': '回踩不破',
        'M1': '20日线', 'M1S': '60日线三态', 'M2': 'MACD金叉', 'M2S': 'MACD 0轴上',
        'V1': '放量突破', 'V1S': '量价健康', 'R1': 'RSI≥50',
        'E1': '板块宽度/场景', 'E1S': '板块场景确认', 'E2': '板块拥挤度',
        'E3': '大盘MA20', 'F1': '非ST/停牌', 'F1F': '业绩恶化',
    }
    return names.get(cid, cid)
