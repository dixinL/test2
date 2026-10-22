"""
test2 总览 RSS 生成（analysis-hub 内部分发脚本）：扫描总览报告 (Markdown)，
生成 RSS 2.0 XML 订阅源。

每个 RSS 条目的链接指向 analysis-hub/report/ 下对应日期的总览文件
(托管在 GitHub Pages)。rss.xml 直接写在 analysis-hub/report/ 目录下。

test2 专用：目录与站点信息为本项目固定配置，由 run.py 分发调度，
也可在 analysis-hub 目录下直接运行（无命令行参数）:
  python generate_rss.py
"""

import os
import re
import sys
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE_DIR, 'report')
SITE_BASE = 'https://dixinl.github.io/test2/analysis-hub'
FEED_TITLE = 'test2 个股右侧判定总览'
FEED_DESC = '每日自动汇总：市场环境快照 + 个股右侧三态判定（弱右侧/确认右侧名单与状态起点）'
REPORT_PREFIX = 'overview_'


def extract_report_info(report_path, report_url):
    """从总览文件中提取日期、标题、描述（市场判断 + rightside 统计行）。"""
    filename = os.path.basename(report_path)
    m = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
    report_date = m.group(1) if m else 'unknown'
    title = 'test2 工具总览 - {}'.format(report_date)
    parts = []
    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            for line in f:
                s = line.strip().lstrip('-* ')
                if s.startswith('**市场判断') or s.startswith('统计:') or s.startswith('统计：'):
                    s = s.replace('**', '')
                    if s:
                        parts.append('<p>{}</p>'.format(xml_escape(s[:120])))
                if len(parts) >= 2:
                    break
    except Exception:
        pass
    parts.append('<p><a href="{url}">查看完整总览：{name}</a></p>'.format(
        url=xml_escape(report_url), name=xml_escape(filename)))
    return report_date, title, '\n'.join(parts)


def generate_rss():
    """生成 RSS 2.0 XML"""
    feed_link = '{}/report/'.format(SITE_BASE)
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_files = [f for f in os.listdir(REPORT_DIR)
                    if f.startswith(REPORT_PREFIX) and f.endswith('.md')]
    if not report_files:
        print('未找到任何总览报告文件 (在 {})'.format(REPORT_DIR))
        return False
    report_files.sort(reverse=True)
    old_pub_dates = {}
    rss_path = os.path.join(REPORT_DIR, 'rss.xml')
    if os.path.exists(rss_path):
        try:
            tree = ET.parse(rss_path)
            channel = tree.getroot().find('channel')
            if channel is not None:
                for item in channel.findall('item'):
                    link, pub = item.find('link'), item.find('pubDate')
                    if link is not None and pub is not None:
                        old_pub_dates[link.text] = pub.text
            print('[INFO] 已读取 {} 条旧的 pubDate'.format(len(old_pub_dates)))
        except Exception:
            pass
    items_xml = []
    is_first = True
    for filename in report_files:
        filepath = os.path.join(REPORT_DIR, filename)
        url = '{}/report/{}'.format(SITE_BASE, filename)
        report_date, title, desc = extract_report_info(filepath, url)
        if is_first:
            pub_date = datetime.now().strftime('%Y-%m-%dT%H:%M:%S+08:00')
            is_first = False
        elif url in old_pub_dates:
            pub_date = old_pub_dates[url]
        else:
            pub_date = datetime.fromtimestamp(
                os.path.getmtime(filepath)).strftime('%Y-%m-%dT%H:%M:%S+08:00')
        items_xml.append(
            '    <item>\n      <title>{title}</title>\n      <link>{url}</link>'
            '\n      <description><![CDATA[{desc}]]></description>'
            '\n      <pubDate>{pub_date}</pubDate>'
            '\n      <guid isPermaLink="true">{url}</guid>\n    </item>'.format(
                title=xml_escape(title), url=xml_escape(url),
                desc=desc.strip(), pub_date=pub_date))
    now = datetime.now().strftime('%a, %d %b %Y %H:%M:%S +0800')
    rss_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        '  <channel>\n'
        '    <title>{feed_title}</title>\n'
        '    <link>{feed_link}</link>\n'
        '    <description>{feed_desc}</description>\n'
        '    <language>zh-CN</language>\n'
        '    <lastBuildDate>{now}</lastBuildDate>\n'
        '    <atom:link href="{feed_link}rss.xml" rel="self" type="application/rss+xml"/>\n'
        '{items}\n'
        '  </channel>\n'
        '</rss>').format(
            feed_title=xml_escape(FEED_TITLE),
            feed_link=xml_escape(feed_link),
            feed_desc=xml_escape(FEED_DESC),
            now=now,
            items='\n'.join(items_xml))
    with open(rss_path, 'w', encoding='utf-8') as f:
        f.write(rss_xml)
    print('RSS 已生成: {} ({} 篇总览)'.format(rss_path, len(report_files)))
    print('订阅地址: {}/report/rss.xml'.format(SITE_BASE))
    return True


if __name__ == '__main__':
    sys.exit(0 if generate_rss() else 1)
