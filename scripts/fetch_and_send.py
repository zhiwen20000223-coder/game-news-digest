#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日手游行业资讯简报
运行环境: GitHub Actions (每天 UTC 1:00 = 北京时间 9:00)
功能: 搜索游戏行业新闻 → 生成 HTML 邮件 → 通过 QQ邮箱 SMTP 发送
"""

import os
import re
import json
import smtplib
import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import feedparser
import requests

# ── 配置 ───────────────────────────────────────────
SMTP_SERVER = "smtp.qq.com"
SMTP_PORT = 465
SENDER = os.environ["QQ_EMAIL"]
PASSWORD = os.environ["QQ_SMTP_AUTH"]
RECEIVER = os.environ["QQ_EMAIL"]

# ── 资讯搜索 ───────────────────────────────────────

def search_google_news(query, hl="zh-CN", gl="CN", max_results=5):
    """通过 Google News RSS 搜索"""
    url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl={hl}&gl={gl}&ceid={gl}:{hl}"
    try:
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries[:max_results]:
            items.append({
                "title": entry.title,
                "link": entry.link,
                "published": entry.get("published", ""),
                "source": entry.get("source", {}).get("title", ""),
                "summary": clean_html(entry.get("summary", ""))[:150]
            })
        return items
    except Exception as e:
        print(f"[WARN] Google News RSS 搜索失败 ({query}): {e}")
        return []


def clean_html(html_text):
    """简单去除 HTML 标签"""
    return re.sub(r"<[^>]+>", "", html_text).strip()


def collect_all_news():
    """收集四大方向的资讯"""
    today = datetime.date.today()

    categories = {
        "🆕 手游新品上线 & 测试": [
            "手游 公测 上线 2026",
            "手游 内测 预约 新游",
            "mobile game new release 2026",
        ],
        "📜 版号与政策动态": [
            "中国 游戏版号 审批 2026",
            "游戏 监管政策 最新",
            "国家新闻出版署 游戏审批",
        ],
        "🌏 出海 & 海外市场": [
            "中国手游出海 海外市场 2026",
            "手游 全球市场 Sensor Tower",
            "mobile game overseas market China",
        ],
        "🤖 AI 在游戏行业的影响": [
            "AI 游戏开发 应用 最新",
            "人工智能 游戏 应用",
            "AI game development news 2026",
        ],
    }

    all_news = {}
    for category, queries in categories.items():
        category_news = []
        seen_titles = set()
        for query in queries:
            results = search_google_news(query, max_results=6)
            for item in results:
                if item["title"] not in seen_titles:
                    seen_titles.add(item["title"])
                    category_news.append(item)
        # 去重后取前 5 条
        all_news[category] = sorted(
            category_news,
            key=lambda x: x.get("published", ""),
            reverse=True
        )[:5]

    return all_news


# ── 生成 HTML 邮件 ────────────────────────────────

def build_html_email(all_news):
    """生成精美的 HTML 邮件内容"""
    today_str = datetime.date.today().strftime("%Y年%m月%d日")

    # 颜色定义
    colors = {
        "🆕 手游新品上线 & 测试": "#FF6B35",
        "📜 版号与政策动态": "#4ECDC4",
        "🌏 出海 & 海外市场": "#1A535C",
        "🤖 AI 在游戏行业的影响": "#7B2DFF",
    }

    sections_html = ""
    for category, items in all_news.items():
        color = colors.get(category, "#333")
        items_html = ""
        if not items:
            items_html = '<li style="color:#999;padding:8px 0;">暂无最新动态，请持续关注</li>'
        else:
            for item in items:
                items_html += f"""
                <li style="margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid #f0f0f0;">
                    <a href="{item['link']}" style="color:#333;text-decoration:none;font-weight:600;font-size:14px;" target="_blank">
                        {item['title']}
                    </a>
                    <div style="color:{color};font-size:12px;margin-top:4px;">
                        📰 {item['source']} · {item.get('published', '')[:25]}
                    </div>
                    <div style="color:#666;font-size:13px;margin-top:4px;line-height:1.5;">
                        {item['summary']}
                    </div>
                </li>
                """

        sections_html += f"""
        <div style="margin-bottom:24px;">
            <h2 style="color:{color};font-size:18px;margin:0 0 12px 0;padding-bottom:8px;border-bottom:2px solid {color};">
                {category} ({len(items)}条)
            </h2>
            <ul style="list-style:none;padding:0;margin:0;">
                {items_html}
            </ul>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background-color:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f5f5f5;padding:20px 0;">
        <tr>
            <td align="center">
                <table width="640" cellpadding="0" cellspacing="0" style="background-color:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
                    <!-- Header -->
                    <tr>
                        <td style="background:linear-gradient(135deg,#667eea,#764ba2);padding:32px 40px;text-align:center;">
                            <h1 style="color:#fff;font-size:26px;margin:0 0 8px 0;">🎮 手游行业资讯简报</h1>
                            <p style="color:rgba(255,255,255,0.85);font-size:14px;margin:0;">{today_str} · 四大方向 · 每日追踪</p>
                        </td>
                    </tr>
                    <!-- Content -->
                    <tr>
                        <td style="padding:32px 40px;">
                            {sections_html}
                        </td>
                    </tr>
                    <!-- Footer -->
                    <tr>
                        <td style="background-color:#fafafa;padding:20px 40px;text-align:center;border-top:1px solid #eee;">
                            <p style="color:#999;font-size:12px;margin:0;">
                                📬 由 GitHub Actions 自动生成 · 数据来源 Google News<br>
                                如有问题请联系我们
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

    return html


def send_email(html_content):
    """通过 QQ邮箱 SMTP 发送邮件"""
    today_str = datetime.date.today().strftime("%Y%m%d")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎮 手游行业资讯简报 | {today_str}"
    msg["From"] = f"游戏资讯助手 <{SENDER}>"
    msg["To"] = RECEIVER

    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(SENDER, PASSWORD)
            server.sendmail(SENDER, [RECEIVER], msg.as_string())
        print(f"✅ 邮件发送成功 → {RECEIVER}")
        return True
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")
        return False


# ── 主流程 ────────────────────────────────────────

def main():
    print("=" * 50)
    print("🎮 手游行业资讯简报生成中...")
    print(f"📅 日期: {datetime.date.today()}")
    print(f"📧 收件人: {RECEIVER}")
    print("=" * 50)

    # 收集资讯
    print("\n🔍 正在搜索行业资讯...")
    all_news = collect_all_news()

    total = sum(len(v) for v in all_news.values())
    print(f"📊 共收集到 {total} 条资讯")

    # 生成邮件
    print("\n📝 生成 HTML 邮件...")
    html_content = build_html_email(all_news)

    # 发送邮件
    print("\n📧 发送邮件...")
    success = send_email(html_content)

    if success:
        print("\n🎉 任务完成!")
    else:
        print("\n⚠️ 任务部分失败")
        exit(1)


if __name__ == "__main__":
    main()
