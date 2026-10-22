"""test1 环境快照增量拉取层（板块/大盘环境数据，供 rightside 消费）。

从 test1 仓库 raw URL 拉取每日环境数据（纯 HTTP 读，不依赖 git clone；直连失败自动回落 jsDelivr CDN 镜像）：
- sw2-daily-report/report/env_summary_YYYY-MM-DD.csv
    板块场景(S1~S9)/信号/宽度/拥挤度 + 市场判断（MARKET 汇总行 + 行业行）
- sw2-trend-analysis/data/exports/sw_trend_summary_YYYY-MM-DD.csv
    行业多窗口趋势方向/斜率/R²/综合分/历史分位

本地缓存 general-data/env/，缺失才下载（重跑零请求）；
404（test1 无该日文件，如非交易日/CI 失败）记 missing 标记避免重复请求。
新鲜度校验：最新 env 日期落后 leaders.db max(trade_date) ≥2 个交易日则告警，
消费方应将环境条件标记"数据过期"降级，不静默使用过期结论。

用法:
    python envfetch.py                  # 增量拉取近 LOOKBACK_DAYS 个交易日
    python envfetch.py --lookback 60    # 指定回看天数
    python envfetch.py --refetch        # 忽略 missing 标记重试 404 的日期
"""
from __future__ import annotations

import argparse
import os
import ssl
import sys
import urllib.request

import leadersdb

BASE_URL = 'https://raw.githubusercontent.com/dixinL/test1/main/'
CDN_BASE = 'https://cdn.jsdelivr.net/gh/dixinL/test1@main/'  # raw 直连失败时的镜像回退
ENV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'env')
LOOKBACK_DAYS = 30
DOWNLOAD_TIMEOUT = 30

# kind -> test1 仓库内路径模板
SOURCES = {
    'env': 'sw2-daily-report/report/env_summary_{d}.csv',
    'trend': 'sw2-trend-analysis/data/exports/sw_trend_summary_{d}.csv',
}


def _ssl_context() -> ssl.SSLContext:
    """优先用 certifi 证书包（Windows 系统证书链可能缺失），无则回退默认。"""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _fetch(url: str, ctx: ssl.SSLContext) -> bytes | None:
    """单次抓取。404 返回 None；其他网络错误抛异常。"""
    req = urllib.request.Request(url, headers={'User-Agent': 'test2-envfetch'})
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT, context=ctx) as resp:
            return resp.read()
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise


def _download(url: str, ctx: ssl.SSLContext) -> bytes | None:
    """下载 URL：先走 raw 直连；被墙/超时（非 404 异常）或 404 时换 jsDelivr
    CDN 镜像重试一次。两处都 404 返回 None（记 missing 标记）；
    CDN 也失败则抛异常由调用方告警。"""
    try:
        data = _fetch(url, ctx)
    except Exception:
        data = None  # 直连不可达等，回落 CDN
    if data is not None:
        return data
    if url.startswith(BASE_URL):
        return _fetch(CDN_BASE + url[len(BASE_URL):], ctx)
    return None


def _calendar(conn, lookback: int) -> list[str]:
    """leaders.db daily 的最近 lookback 个交易日（升序返回）。"""
    rows = conn.execute(
        'SELECT DISTINCT trade_date FROM daily ORDER BY trade_date DESC LIMIT ?',
        (lookback,),
    ).fetchall()
    return sorted(r['trade_date'] for r in rows)


def fetch_env(conn, lookback: int = LOOKBACK_DAYS, refetch: bool = False) -> dict:
    """增量拉取环境快照。返回 {'downloaded': n, 'missing': n, 'cached': n, 'failed': n}。"""
    os.makedirs(ENV_DIR, exist_ok=True)
    ctx = _ssl_context()
    dates = _calendar(conn, lookback)
    if not dates:
        print('[envfetch] [ERROR] leaders.db daily 为空，请先运行 refresh.py')
        return {'downloaded': 0, 'missing': 0, 'cached': 0, 'failed': 0}

    stats = {'downloaded': 0, 'missing': 0, 'cached': 0, 'failed': 0}
    for d in dates:
        for kind, tmpl in SOURCES.items():
            path = os.path.join(ENV_DIR, '{}_{}.csv'.format(kind, d))
            marker = os.path.join(ENV_DIR, '.missing_{}_{}'.format(kind, d))
            if os.path.exists(path):
                stats['cached'] += 1
                continue
            if os.path.exists(marker) and not refetch:
                stats['missing'] += 1
                continue
            url = BASE_URL + tmpl.format(d=d)
            try:
                data = _download(url, ctx)
            except Exception as err:
                stats['failed'] += 1
                print('  [WARN] {} {}: {}'.format(kind, d, err))
                continue
            if data is None:
                # test1 无该日文件（非交易日/CI 失败），记标记跳过
                with open(marker, 'w', encoding='utf-8') as f:
                    f.write(url)
                stats['missing'] += 1
                continue
            with open(path, 'wb') as f:
                f.write(data)
            if os.path.exists(marker):
                os.remove(marker)
            stats['downloaded'] += 1
    print('[envfetch] 交易日 {} 个 · 新下载 {} · 缓存命中 {} · 无文件 {} · 失败 {}'.format(
        len(dates), stats['downloaded'], stats['cached'], stats['missing'], stats['failed']))
    return stats


def check_freshness(conn) -> bool:
    """新鲜度校验：最新 env_summary 日期落后库内最新交易日 ≥2 个交易日则告警。
    返回 True=新鲜（或无环境数据），False=过期。"""
    max_daily = conn.execute('SELECT MAX(trade_date) AS d FROM daily').fetchone()['d']
    env_files = sorted(
        f for f in os.listdir(ENV_DIR)
        if f.startswith('env_') and f.endswith('.csv')
    ) if os.path.isdir(ENV_DIR) else []
    if not max_daily:
        return True
    if not env_files:
        print('[envfetch] [WARN] 无任何环境快照，环境条件将以"数据缺失"处理')
        return False
    latest_env = env_files[-1][4:-4]  # env_YYYY-MM-DD.csv -> YYYY-MM-DD
    if latest_env >= max_daily:
        return True
    cal = _calendar(conn, LOOKBACK_DAYS * 3)
    if latest_env in cal and max_daily in cal:
        gap = cal.index(max_daily) - cal.index(latest_env)
    else:
        gap = 3  # 日期对不上时按过期处理
    if gap >= 2:
        print('[envfetch] [WARN] 环境快照落后 {} 个交易日 (env={} vs daily={})，'
              '环境条件将标记"数据过期"降级'.format(gap, latest_env, max_daily))
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description='test1 环境快照增量拉取')
    parser.add_argument('--lookback', type=int, default=LOOKBACK_DAYS,
                        help='回看交易日数（默认 {}）'.format(LOOKBACK_DAYS))
    parser.add_argument('--refetch', action='store_true',
                        help='忽略 missing 标记，重试此前 404 的日期')
    args = parser.parse_args()

    conn = leadersdb.connect()
    try:
        fetch_env(conn, lookback=args.lookback, refetch=args.refetch)
        check_freshness(conn)
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
