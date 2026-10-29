"""test2 工具总览生成器。

汇总各工具最新产物，生成 analysis-hub/report/overview_<date>.md。
面向多工具设计：TOOLS 注册表驱动，新增工具时注册一个 section 构建器即可。

已注册工具:
  rightside -> 个股右侧判定（消费 rightside/report/rightside_states.csv
               与 rightside/db.load_env 的市场环境）

用法:
  python overview.py                # 取各工具产物中最新日期生成总览
  python overview.py --date 2026-10-15
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
REPORT_DIR = os.path.join(BASE_DIR, 'report')
sys.path.insert(0, os.path.join(ROOT_DIR, 'general-data'))
sys.path.insert(0, os.path.join(ROOT_DIR, 'rightside'))

import db as rightside_db  # noqa: E402
import leadersdb  # noqa: E402
import conditions as rightside_conditions  # noqa: E402
import rightside  # noqa: E402
import states  # noqa: E402

STATE_ORDER = ['确认右侧', '弱右侧', '价格达标(环境缺失)', '左侧', '排除']

# 近30日走势/量能色块（单行 8 档高度，每日 1 格 × 30 格；在表外代码块中展示，不挤表格）
BLOCKS = '▁▂▃▄▅▆▇█'
CHART_DAYS = 30


def _bars(vals):
    """最近 CHART_DAYS 日逐日色块曲线（8 档高度，相对窗口内自身区间归一）。"""
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        hi = lo + 1
    return ''.join(BLOCKS[min(int(round((v - lo) / (hi - lo) * 7)), 7)]
                   for v in vals)


def _spark_close(closes):
    return _bars(closes[-CHART_DAYS:])


def _spark_vol(vols):
    return _bars(vols[-CHART_DAYS:])


def _latest_date() -> str | None:
    """各工具产物中的最新日期（当前 = states.csv 最新记录日）。"""
    rows = []
    if os.path.exists(states.STATES_CSV):
        import csv
        with open(states.STATES_CSV, 'r', encoding='utf-8', newline='') as f:
            rows = [r['date'] for r in csv.DictReader(f)]
    return max(rows) if rows else None


def _market_section(target, lines):
    env_date, market, _inds = rightside_db.load_env(target)
    lines += ['## 市场环境（test1 快照）', '']
    if market:
        if env_date != target:
            lines.append('- ⚠ 环境快照截至 {}（落后判定日）'.format(env_date))
        lines += [
            '- **市场判断: {}**'.format(market.get('market_judgment')),
            '- 宽度均值 {} ({}) | 拥挤度均值 {} ({})'.format(
                market.get('avg_width'), market.get('width_state'),
                market.get('avg_congestion'), market.get('congestion_state')),
            '',
        ]
    else:
        lines += ['- 无环境快照（先运行 general-data/envfetch.py）', '']
    return lines


def _collect(target, day):
    """对确认/弱右侧名单逐股现算明细与近30日色块图。

    details[code] = (状态起点, 现收, 价图, 量图, 距60日高, 距MA20, 未过强档)。"""
    env_date, _market, industries = rightside_db.load_env(target)
    trend_date, trends = rightside_db.load_trend(target)
    env_bundle = {'env_date': env_date, 'market': _market,
                  'industries': industries, 'trend_date': trend_date,
                  'trends': trends}
    conn = leadersdb.connect()
    details = {}
    try:
        for code, name, sw2, state in day:
            if state not in ('确认右侧', '弱右侧'):
                continue
            ctx = rightside.build_context(conn, code, sw2, target, env_bundle)
            if ctx.get('status_ok') is False:
                details[code] = ('-', '', '', '', '', '', '-')
                continue
            i = ctx['i']
            close = ctx['close'][i]
            ma20 = ctx['ma20'][i]
            hi60 = max(ctx['high'][i - rightside_conditions.BOX_WINDOW + 1:i + 1])
            d_top = '{:+.1%}'.format(close / hi60 - 1)
            d_ma = '{:+.1%}'.format(close / ma20 - 1)
            spark = _spark_close(ctx['close'])
            vspark = _spark_vol(ctx['vol'])
            sd, days, bounded = rightside.state_start_real(ctx, state)
            start = rightside.fmt_start(sd, days, bounded)
            results = rightside_conditions.evaluate(ctx)
            fails = rightside.failed_summary(results, state) or '—'
            if ctx.get('earnings_note'):
                fails = (fails + '；' if fails != '—' else '') + ctx['earnings_note']
            details[code] = (start, '{:.2f}'.format(close), spark, vspark,
                             d_top, d_ma, fails)
    finally:
        conn.close()
    return details


def _rightside_section(target, lines):
    day = states.load_day(target)
    lines += ['## 个股右侧判定 (rightside)', '']
    if not day:
        lines += ['- 无判定记录（先运行 rightside/run.py）', '']
        return lines

    # 逐股现算：状态起点 + 个股明细（现收、近30日色块图、距60日高/MA20、未过强档），
    # 只对确认/弱右侧名单计算（数量少）
    details = _collect(target, day)

    groups = {s: [] for s in STATE_ORDER}
    for code, name, sw2, state in day:
        st, close, spark, vspark, d_top, d_ma, fails = details.get(
            code, ('-', '', '', '', '', '', '-'))
        groups[state].append((code, name, sw2, st, close, spark, vspark,
                              d_top, d_ma, fails))

    counts = ' · '.join('{} {}只'.format(s, len(groups[s]))
                        for s in STATE_ORDER if groups[s])
    fresh = {s: [r for r in groups[s] if r[3].startswith(target[5:])]
             for s in ('确认右侧', '弱右侧')}
    lines += [
        '- 统计: {}'.format(counts),
        '- 今日新增: 确认右侧 {} 只 · 弱右侧 {} 只'.format(
            len(fresh['确认右侧']), len(fresh['弱右侧'])),
        '- 详细报告: ../rightside/report/rightside_{}.md'.format(target),
        '',
    ]
    for s in ('确认右侧', '弱右侧'):
        lines += ['### {}（{} 只）'.format(s, len(groups[s])), '']
        if not groups[s]:
            lines += ['无', '']
            continue
        lines += ['| 代码 | 名称 | 申万二级 | 状态起点 | 现收 | 距60日高 | 距MA20 | 未过强档 |',
                  '|---|---|---|---|---|---|---|---|']
        for code, name, sw2, st, close, spark, vspark, d_top, d_ma, fails in groups[s]:
            lines.append('| {} | {} | {} | {} | {} | {} | {} | {} |'.format(
                code, name, sw2, st, close, d_top, d_ma, fails))
        lines.append('')
        # 近30日走势/量能色块图（单行 8 档，每日 1 格，等宽代码块）
        for code, name, sw2, st, close, spark, vspark, d_top, d_ma, fails in groups[s]:
            if not spark:
                continue
            lines += ['**{} {}**（{} · {} · 现收 {}）'.format(code, name, s, st, close),
                      '', '```text',
                      '价 ' + spark,
                      '量 ' + vspark,
                      '```', '']
    lines += ['> 注：现收/MA20 为前复权口径；个股图为近30日色块曲线（8 档，每日 1 格，'
              '价/量各自相对窗口内自身区间归一，左旧右新）；距60日高=现收相对近60日最高价偏离（回撤深度），'
              '距MA20=现收相对 20 日均线偏离；未过强档 = 尚未达到确认右侧的强档条件（'
              '详见 rightside/规则.md）。', '']
    return lines


# 注册表：新增工具时加一项 (标题, section 构建器)
SECTIONS = [
    ('rightside', _rightside_section),
]


def generate(target: str) -> str:
    os.makedirs(REPORT_DIR, exist_ok=True)
    lines = [
        '# test2 工具总览 ({})'.format(target),
        '',
        '> 各工具判定口径见各自模块文档；本页为汇总视图，不构成投资建议。',
    ]
    lines = _market_section(target, lines)
    for _name, builder in SECTIONS:
        lines = builder(target, lines)
    lines += ['---', '',
              '*生成时间: {}*'.format(
                  datetime.now().strftime('%Y-%m-%d %H:%M'))]
    path = os.path.join(REPORT_DIR, 'overview_{}.md'.format(target))
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description='test2 工具总览')
    parser.add_argument('--date', type=str, default=None,
                        help='总览日期 YYYY-MM-DD（默认取工具产物最新）')
    args = parser.parse_args()

    target = args.date or _latest_date()
    if not target:
        print('[overview] [ERROR] 无工具产物，请先运行工具')
        return 1
    path = generate(target)
    print('[overview] 总览已生成: {}'.format(path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
