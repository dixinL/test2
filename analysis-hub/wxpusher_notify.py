# -*- coding: utf-8 -*-
"""WxPusher 微信推送通知（test2 analysis-hub 版）。

推送最新的工具总览（analysis-hub/report/overview_YYYY-MM-DD.md）。

用法（供 run.py 分发调度，也可手动运行）:
  python wxpusher_notify.py                     # 自动找最新总览
  python wxpusher_notify.py --date 2026-10-21   # 指定日期
  python wxpusher_notify.py report/overview_xxx.md

环境变量(必填，代码不内置明文):
  WXPUSHER_APP_TOKEN: WxPusher appToken
  WXPUSHER_UIDS:      接收者 UID(逗号分隔)
  未配置时跳过推送并以非零码退出（run.py 中仅告警，不影响主流程）。

推送形态：整篇总览渲染为 HTML（contentType=2，标题/表格带内联样式），
超过上限按最小标题从后往前整段删除（H1 永不删，极端兜底字节硬截断）。
摘要格式：日期 | 市场判断 | rightside统计。
"""

import os
import re
import sys
import html as _html
import json as _json
import urllib.request as _urllib_req
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE_DIR, "report")

# ====== 配置：只读环境变量，明文不入代码（CI 用 secrets 注入）======
WXPUSHER_APP_TOKEN = os.environ.get("WXPUSHER_APP_TOKEN", "")
WXPUSHER_UIDS = [u.strip() for u in os.environ.get("WXPUSHER_UIDS", "").split(",") if u.strip()]
SITE_URL = "https://dixinl.github.io/test2/analysis-hub"
MAX_CONTENT_BYTES = 36000  # WxPusher 硬上限 40000 字节, 留余量
# ==========================================

REPORT_PREFIX = "overview_"


def _latest():
    """目录下按文件名倒序取最新一个 overview_*.md，无则返回 None。"""
    if not os.path.isdir(REPORT_DIR):
        return None
    md_files = sorted(f for f in os.listdir(REPORT_DIR)
                      if f.startswith(REPORT_PREFIX) and f.endswith(".md"))
    return os.path.join(REPORT_DIR, md_files[-1]) if md_files else None


def find_report_by_date(target_date):
    path = os.path.join(REPORT_DIR, "{}{}.md".format(REPORT_PREFIX, target_date))
    return path if os.path.exists(path) else None


def extract_date_from_filename(filepath):
    m = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(filepath))
    return m.group(1) if m else datetime.now().strftime("%Y-%m-%d")


def _trim_report(md, max_bytes):
    """全文超上限时的降级：按最小标题从后往前整段删除，直到不超限。

    先删最小级标题的段落，该级删完仍超则逐级放大（###、##）；
    H1 报告名永不删；极端兜底按 UTF-8 字节硬截断。"""
    lines = md.rstrip().splitlines()

    def blen(ls):
        return len("\n".join(ls).encode("utf-8"))

    for level in (4, 3, 2):
        while blen(lines) > max_bytes:
            starts = [i for i, ln in enumerate(lines)
                      if re.match(r'^' + '#' * level + r'\s', ln)]
            if not starts:
                break
            s = starts[-1]
            e = len(lines)
            for j in range(s + 1, len(lines)):
                m = re.match(r'^(#{1,6})\s', lines[j])
                if m and len(m.group(1)) <= level:
                    e = j
                    break
            del lines[s:e]
            while lines and not lines[-1].strip():
                lines.pop()
        if blen(lines) <= max_bytes:
            break

    out = "\n".join(lines)
    if len(out.encode("utf-8")) > max_bytes:
        out = out.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    return out


def _inline_html(s):
    """行内 Markdown（加粗/链接）转 HTML，先转义防注入。"""
    s = _html.escape(s)
    s = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', s)
    s = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', s)
    return s


_TD_STYLE = 'border:1px solid #ccc;font-size:12px;'


def _build_html_table(rows):
    """表格块 -> HTML <table>（首行作表头，带边框与底色）。"""
    if not rows:
        return ''
    parts = ['<table style="border-collapse:collapse;margin:4px 0;">']
    for ri, cells in enumerate(rows):
        tag = 'th' if ri == 0 else 'td'
        extra = 'background:#eef2f7;' if ri == 0 else ''
        parts.append('<tr>')
        for c in cells:
            parts.append('<{0} style="{1}{2}">{3}</{0}>'.format(
                tag, _TD_STYLE, extra, _inline_html(c)))
        parts.append('</tr>')
    parts.append('</table>')
    return ''.join(parts)


def _md_to_html(md):
    """Markdown 总览 -> WxPusher HTML（contentType=2）。

    颜色方案：H1 红 / H2 蓝灰 / H3 蓝；⚠ 开头的告警行橙色；引用灰斜体；
    表格转真实 <table>（边框+表头底色）。"""
    out = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        t = lines[i].strip()
        # 表格块 -> 真实 <table>（跳过 |---| 分隔行）
        if t.startswith('|') and t.endswith('|'):
            rows = []
            while i < len(lines):
                t2 = lines[i].strip()
                if not (t2.startswith('|') and t2.endswith('|')):
                    break
                cells = [c.strip() for c in t2.strip('|').split('|')]
                if not all(re.fullmatch(r':?-{2,}:?', c) for c in cells if c):
                    rows.append(cells)
                i += 1
            out.append(_build_html_table(rows))
            continue
        if not t:
            out.append('<br/>')
            i += 1
            continue
        if t == '---':
            out.append('<hr/>')
            i += 1
            continue
        hm = re.match(r'^(#{1,6})\s+(.*)$', t)
        if hm:
            level = len(hm.group(1))
            color = {1: '#c0392b', 2: '#34495e'}.get(level, '#1a5276')
            size = {1: 20, 2: 17}.get(level, 15)
            lv = min(level + 1, 6)
            out.append('<h{0} style="color:{1};font-size:{2}px;'
                       'border-bottom:1px solid #ddd;padding-bottom:2px;margin:10px 0 4px;">{3}</h{0}>'.format(
                           lv, color, size, _inline_html(hm.group(2))))
            i += 1
            continue
        if t.startswith('>'):
            out.append('<p style="color:#888;margin:2px 0;"><i>' + _inline_html(t.lstrip('> ')) + '</i></p>')
            i += 1
            continue
        body = t[2:] if t.startswith('- ') else t
        bullet = '• ' if t.startswith('- ') else ''
        color = 'color:#ca6f1e;' if '⚠' in body else ''
        out.append('<p style="margin:2px 0;{}">{}{}</p>'.format(color, bullet, _inline_html(body)))
        i += 1
    return ''.join(out)


def _extract_summary_fields(full_report):
    """摘要要素：市场判断 + rightside 统计（确认/弱右侧只数）。"""
    market = ""
    for line in full_report.splitlines():
        s = line.strip().lstrip('-* ')
        if s.startswith('**市场判断'):
            market = s.replace('**', '').split(':', 1)[-1].split('：', 1)[-1].strip()[:30]
            break
    stat = ""
    for line in full_report.splitlines():
        s = line.strip().lstrip('-* ')
        if s.startswith('统计:') or s.startswith('统计：'):
            stat = s.replace('统计:', '').replace('统计：', '').replace('只', '只').strip()[:40]
            break
    return market, stat


def send_wxpusher(report_path, data_date):
    """通过 WxPusher 推送微信消息"""
    if not report_path or not os.path.exists(report_path):
        print("  [WxPusher] 跳过: 报告文件不存在")
        return False

    with open(report_path, "r", encoding="utf-8") as f:
        full_report = f.read()

    file_size = len(full_report.encode("utf-8"))
    print("  [WxPusher] 总览大小: {:.0f}KB".format(file_size / 1024))

    market, stat = _extract_summary_fields(full_report)

    tail_link = '<hr/><p><a href="{0}">查看完整总览</a> | 数据截止: {1}</p>'.format(
        SITE_URL, data_date)
    content = _md_to_html(full_report.rstrip()) + tail_link
    if len(content.encode("utf-8")) <= MAX_CONTENT_BYTES:
        print("  [WxPusher] 模式: 整篇推送全文 (HTML {:.0f}KB)".format(
            len(content.encode("utf-8")) / 1024))
    else:
        budget = MAX_CONTENT_BYTES - len(tail_link.encode("utf-8")) - 500
        trimmed_md = full_report
        while True:
            trimmed_md = _trim_report(full_report.rstrip(), budget)
            content = _md_to_html(trimmed_md) + tail_link
            if len(content.encode("utf-8")) <= MAX_CONTENT_BYTES or budget < 1000:
                break
            budget = int(budget * 0.9)
        print("  [WxPusher] 模式: 逐段删减 (全文 {:.0f}KB -> HTML {:.0f}KB)".format(
            file_size / 1024, len(content.encode("utf-8")) / 1024))

    actual_size = len(content.encode("utf-8"))
    print("  [WxPusher] 推送大小: {:.0f}KB / 限制 {:.0f}KB".format(
        actual_size / 1024, MAX_CONTENT_BYTES / 1024))

    summary = "{} | {}".format(data_date, market) if market else data_date
    if stat:
        summary += " | " + stat
    payload = {
        "appToken": WXPUSHER_APP_TOKEN,
        "content": content,
        "summary": summary[:99],
        "contentType": 2,          # 2=HTML（带内联颜色）
        "topicIds": [],
        "uids": WXPUSHER_UIDS,
        "url": SITE_URL,
    }

    try:
        req_data = _json.dumps(payload).encode("utf-8")
        req = _urllib_req.Request(
            "https://wxpusher.zjiecode.com/api/send/message",
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Windows 下系统证书链可能缺失导致 CERTIFICATE_VERIFY_FAILED，
        # 优先用 certifi 证书包，失败回退默认。
        ssl_ctx = None
        try:
            import ssl
            import certifi
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass
        resp = _urllib_req.urlopen(req, timeout=15, context=ssl_ctx)
        result = _json.loads(resp.read().decode("utf-8"))

        if isinstance(result, list):
            code = result[0].get("code") if result else -1
            msg = result[0].get("msg", "unknown") if result else "empty"
        elif isinstance(result, dict):
            code = result.get("code", -1)
            msg = result.get("msg", "unknown")
        else:
            code, msg = -1, str(type(result))

        if code == 1000:
            print("  [WxPusher] OK -> {} 人, 内容 {:.0f}KB".format(
                len(WXPUSHER_UIDS), actual_size / 1024))
            return True
        print("  [WxPusher] FAIL -> code={}, msg={}".format(code, msg))
        return False
    except Exception as e:
        print("  [WxPusher] ERROR -> {}".format(e))
        return False


def main():
    if not WXPUSHER_APP_TOKEN or not WXPUSHER_UIDS:
        print("[WxPusher] 未配置 WXPUSHER_APP_TOKEN / WXPUSHER_UIDS 环境变量，跳过推送")
        sys.exit(1)

    report_path = None
    data_date = None
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--date" and i + 1 < len(sys.argv):
            data_date = sys.argv[i + 1]
            i += 2
        elif arg.endswith(".md"):
            report_path = arg
            i += 1
        else:
            i += 1

    if report_path is None:
        report_path = find_report_by_date(data_date) if data_date else _latest()
    if report_path is None:
        print("[WxPusher] ERROR: 找不到总览文件")
        sys.exit(1)
    if data_date is None:
        data_date = extract_date_from_filename(report_path)

    print("[WxPusher] 总览文件: {}".format(report_path))
    print("[WxPusher] 数据日期: {}".format(data_date))
    ok = send_wxpusher(report_path, data_date)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
