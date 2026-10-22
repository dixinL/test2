"""rightside 命令行入口。

用法:
    python run.py                        # 全量龙头，默认最新交易日，输出 md 报告
    python run.py --date 2026-10-09      # 指定判定日
    python run.py --code 600519          # 单股明细（逐条条件 ✅/❌ + 明细值）
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(BASE_DIR), 'general-data'))

import conditions  # noqa: E402
import db  # noqa: E402
import leadersdb  # noqa: E402
import rightside  # noqa: E402
import states  # noqa: E402

REPORT_DIR = os.path.join(BASE_DIR, 'report')

STATE_ORDER = ['确认右侧', '弱右侧', '价格达标(环境缺失)', '左侧', '排除']


def _env_note(env_date: str | None, target: str) -> str:
    if not env_date:
        return '无环境快照（先运行 general-data/envfetch.py）'
    if env_date == target:
        return '环境快照: {}（当日）'.format(env_date)
    return '环境快照: {}（落后于判定日，已降级标注）'.format(env_date)


def print_detail(code: str, results: dict, state: str, trend_row: dict | None):
    """单股明细：逐条条件 + 明细值。"""
    print('\n=== {} 判定结果: {} ==='.format(code, state))
    order = ['F1', 'P1', 'P1S', 'P2', 'P2S', 'M1', 'M1S', 'M2', 'M2S',
             'V1', 'V1S', 'R1', 'E1', 'E1S', 'E2', 'E3', 'F1F']
    for cid in order:
        r = results.get(cid)
        if not r:
            continue
        print('  {} {:<12} {}'.format(
            conditions._c(r['ok']), conditions.fmt_id(cid), r['detail']))
    if trend_row:
        print('  [板块趋势] 20日 {} 斜率{:.4f} | 60日 {} 斜率{:.4f} | 综合分 {} 历史分位 {}'.format(
            trend_row.get('dir_20'), trend_row.get('slope_20', 0),
            trend_row.get('dir_60'), trend_row.get('slope_60', 0),
            trend_row.get('综合强度分'), trend_row.get('自身历史分位')))


def run_all(conn, target: str) -> int:
    env_date, market, industries = db.load_env(target)
    trend_date, trends = db.load_trend(target)
    env_bundle = {'env_date': env_date, 'market': market, 'industries': industries,
                  'trend_date': trend_date, 'trends': trends}
    print('[rightside] 判定日: {} | {}'.format(target, _env_note(env_date, target)))
    if market:
        print('[rightside] 市场判断: 宽度均值 {} ({}) | 拥挤度均值 {} ({}) | {}'.format(
            market.get('avg_width'), market.get('width_state'),
            market.get('avg_congestion'), market.get('congestion_state'),
            market.get('market_judgment')))

    leaders = [l for l in db.load_leaders(conn)
               if l['exchange'] in ('SH', 'SZ')]
    rows, unmatched, starts = [], set(), {}
    for l in leaders:
        ctx = rightside.build_context(conn, l['code'], l['sw2'], target, env_bundle)
        if ctx.get('status_ok') is False:
            rows.append((l['code'], l['name'], l['sw2'] or '', '排除',
                         ctx.get('status_note', '')))
            continue
        if ctx['env_row'] is None and env_date:
            unmatched.add(ctx['env_note'])
        results = conditions.evaluate(ctx)
        state = rightside.classify(results)
        if state == '排除':
            note = results['F1']['detail']
        else:
            note = rightside.failed_summary(results, state)
            if ctx.get('earnings_note'):
                note = (note + '；' if note else '') + ctx['earnings_note']
            # 状态起点：基础数据现算（与状态列同口径的三态连续段）。
            # 左侧是兜底态（弱档任一 ❌ 即左），连续段无信息量，不显示起点
            if state == '左侧':
                starts[l['code']] = '-'
            else:
                sd, days, bounded = rightside.state_start_real(ctx, state)
                starts[l['code']] = rightside.fmt_start(sd, days, bounded)
        rows.append((l['code'], l['name'], l['sw2'] or '', state, note))

    # 控制台摘要
    print('\n[rightside] === 判定摘要 (共 {} 只) ==='.format(len(rows)))
    for s in STATE_ORDER:
        n = sum(1 for r in rows if r[3] == s)
        print('  {:<14} {:>4} 只'.format(s, n))

    # 状态日期持久化：按判定日幂等重写 CSV（每日观察记录，供总览/跨日统计）
    states.save_states(
        target, [(r[0], r[1], r[2], r[3]) for r in rows])

    _write_report(target, rows, env_date, market, trend_date, unmatched, starts)
    return 0


def _write_report(target, rows, env_date, market, trend_date, unmatched,
                  starts=None) -> None:
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, 'rightside_{}.md'.format(target))
    lines = [
        '# 个股右侧区间判定 ({})'.format(target),
        '',
        '> 判定口径: 左侧/弱右侧/确认右侧 三态（技术信号存在假突破，不构成投资建议）',
        '> 环境: {} | 趋势快照: {}'.format(
            _env_note(env_date, target) if env_date else '无环境快照',
            trend_date or '无'),
        '',
    ]
    if market:
        lines += [
            '## 市场环境（test1 结论）',
            '',
            '- 宽度均值 {} ({}) | 拥挤度均值 {} ({})'.format(
                market.get('avg_width'), market.get('width_state'),
                market.get('avg_congestion'), market.get('congestion_state')),
            '- **市场判断: {}**'.format(market.get('market_judgment')),
            '',
        ]
    for s in STATE_ORDER:
        group = [r for r in rows if r[3] == s]
        if not group:
            continue
        lines += ['## {}（{} 只）'.format(s, len(group)), '',
                  '| 代码 | 名称 | 申万二级 | 状态起点 | 关键说明 |',
                  '|---|---|---|---|---|']
        for code, name, sw2, _, note in group:
            st = (starts or {}).get(code, '-')
            lines.append('| {} | {} | {} | {} | {} |'.format(
                code, name, sw2, st, note or '-'))
        lines.append('')
    if unmatched:
        lines += ['## ⚠ 行业名称对不上（需在 rightside/name_map.py 补映射）', '']
        lines += ['- {}'.format(u) for u in sorted(unmatched)]
        lines.append('')
    lines += ['---', '', '*生成时间: {}*'.format(datetime.now().strftime('%Y-%m-%d %H:%M'))]
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print('\n[rightside] 报告已保存: {}'.format(path))


def main() -> int:
    parser = argparse.ArgumentParser(description='个股右侧区间判定')
    parser.add_argument('--date', type=str, default=None, help='判定日 YYYY-MM-DD')
    parser.add_argument('--code', type=str, default=None, help='单股 6 位代码（明细模式）')
    args = parser.parse_args()

    conn = leadersdb.connect()
    try:
        target = args.date or db.get_target_date(conn)
        if not target:
            print('[ERROR] leaders.db 为空，请先运行 general-data/refresh.py')
            return 1
        env_date, market, industries = db.load_env(target)
        trend_date, trends = db.load_trend(target)
        env_bundle = {'env_date': env_date, 'market': market,
                      'industries': industries, 'trend_date': trend_date,
                      'trends': trends}
        if args.code:
            leader = conn.execute(
                'SELECT code, name, sw1, sw2, exchange FROM leaders WHERE code = ?',
                (args.code,)).fetchone()
            if not leader:
                print('[ERROR] 维表中无 {}'.format(args.code))
                return 1
            ctx = rightside.build_context(
                conn, leader['code'], leader['sw2'], target, env_bundle)
            if ctx.get('status_ok') is False:
                print('[ERROR] {}'.format(ctx.get('status_note')))
                return 1
            results = conditions.evaluate(ctx)
            state = rightside.classify(results)
            trend_row = trends.get(ctx['env_row']['sw_name']) if ctx['env_row'] else None
            print_detail(leader['code'] + ' ' + leader['name'], results, state, trend_row)
            return 0
        return run_all(conn, target)
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
