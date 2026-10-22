# -*- coding: utf-8 -*-
"""test2 analysis-hub 调度入口：生成总览 → 分发（RSS + WxPusher）。

与 test1 同约定：
- RSS 默认生成（--no-rss 关闭）
- WxPusher 默认推送（--no-notify 关闭；--notify 保留兼容，等价于默认行为）
- --rss-only / --notify-only 不重新生成总览，仅用现有产物分发
- RSS 失败计入退出码；推送失败仅告警，不影响主流程

用法:
  python run.py                    # 生成最新总览 + RSS + 推送
  python run.py --date 2026-10-21  # 指定日期
  python run.py --no-notify        # 生成总览+RSS 但不推送
  python run.py --rss-only         # 仅用现有产物重新生成 RSS
  python run.py --notify-only      # 仅推送最新总览
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import generate_rss  # noqa: E402
import wxpusher_notify  # noqa: E402


def run_rss() -> bool:
    try:
        return generate_rss.generate_rss()
    except Exception as e:
        print('[hub] RSS 生成异常: {}'.format(e))
        return False


def run_notify(target=None):
    """推送总览；未配置密钥时 wxpusher_notify 以非零码退出，仅告警。"""
    argv = ['wxpusher_notify.py']
    if target:
        argv += ['--date', target]
    sys.argv = argv
    try:
        wxpusher_notify.main()
    except SystemExit as e:
        if e.code not in (0, None):
            print('[hub] WxPusher 推送未成功（告警，不影响主流程）')


def generate_overview(target=None):
    import overview
    t = target or overview._latest_date()
    if not t:
        print('[hub] [ERROR] 无工具产物，请先运行 rightside/run.py')
        return None
    path = overview.generate(t)
    print('[hub] 总览已生成: {}'.format(path))
    return t


def main(argv):
    date = None
    if '--date' in argv:
        i = argv.index('--date')
        if i + 1 < len(argv):
            date = argv[i + 1]

    rss_only = '--rss-only' in argv
    notify_only = '--notify-only' in argv

    if rss_only or notify_only:
        ok = True
        if rss_only and not run_rss():
            return 1
        if notify_only:
            run_notify(date)
        return 0 if ok else 1

    no_rss = '--no-rss' in argv
    no_notify = '--no-notify' in argv

    target = generate_overview(date)
    if not target:
        return 1

    if not no_rss:
        if not run_rss():
            return 1
    else:
        print('[hub] 跳过 RSS 生成 (--no-rss)')

    if not no_notify:
        run_notify(target)
    else:
        print('[hub] 跳过 WxPusher 推送 (--no-notify)')

    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
