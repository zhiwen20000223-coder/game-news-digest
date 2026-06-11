#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日手游行业资讯简报 v2
运行环境: GitHub Actions (每天 UTC 1:00 = 北京时间 9:00)
策略: 多源搜索 + 硬日期过滤 → 生成 HTML 邮件 → QQ邮箱 SMTP 发送
"""

import os
import re
import json
import smtplib
import datetime
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from urllib.parse import quote

import feedparser
import requests

# ── 配置 ───────────────────────────────────────────
SMTP_SERVER = "smtp.qq.com"
SMTP_PORT = 465
SENDER = os.environ["QQ_EMAIL"]
PASSWORD = os.environ["QQ_SMTP_AUTH"]
RECEIVER = os.environ["QQ_EMAIL"]

# 时效性：只取最近多少天内的新闻
MAX_AGE_DAYS = 30

# 每方向最多保留条数
MAX_PER_CATEGORY = 6

# ── 工具函数 ──────────────────────────────────────

def clean_html(text):
    return re.sub(r"<[^>]+>", "", text).strip()


def parse_date(date_str):
    """尝试解析各种日期格式，返回 datetime 对象"""
    if not date_str:
        return None
    # RFC 2822 格式 (最常见的 RSS 格式)
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.datetime.strptime(date_str.strip(), fmt)
        except (ValueError, TypeError):
            continue

    # feedparser 的 parsed 字段
    try:
        # 可能是 time.struct_time
        if hasattr(date_str, 'tm_year'):
            return datetime.datetime.fromtimestamp(time.mktime(date_str))
    except Exception:
        pass

    return None


def is_recent(date_obj, max_days=MAX_AGE_DAYS):
    """判断日期是否在 max_days 天内"""
    if date_obj is None:
        return False
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=max_days)
    if date_obj.tzinfo is None:
        date_obj = date_obj.replace(tzinfo=datetime.timezone.utc)
    return date_obj >= cutoff


# ── 多源搜索 ──────────────────────────────────────

def search_google_news_rss(query, hl="zh-CN", gl="CN", max_results=8):
    """Google News RSS — 免费、快速"""
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl={hl}&gl={gl}&ceid={gl}:{hl}"
    try:
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries[:max_results]:
            # 优先用 published_parsed
            pub_date = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub_date = entry.published_parsed
            elif hasattr(entry, "published"):
                pub_date = entry.published

            parsed = parse_date(pub_date) if pub_date else None
            if parsed and not is_recent(parsed):
                continue  # 时间太久，跳过

            items.append({
                "title": clean_html(entry.get("title", "")),
                "link": entry.get("link", ""),
                "published": parsed.strftime("%Y-%m-%d") if parsed else (entry.get("published", "")[:25]),
                "source": entry.get("source", {}).get("title", "网络来源"),
                "summary": clean_html(entry.get("summary", ""))[:200],
                "date_obj": parsed,
            })
        return items
    except Exception as e:
        print(f"  [WARN] Google News RSS 失败 ({query[:30]}): {e}")
        return []


def search_bing_news(query, max_results=5):
    """Bing News 搜索 — 补充源，时效性通常更好"""
    # Bing News 搜索 (无 API key 的方式)
    url = f"https://www.bing.com/news/search?q={quote(query)}&format=rss"
    try:
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries[:max_results]:
            pub_date = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub_date = entry.published_parsed
            elif hasattr(entry, "published"):
                pub_date = entry.published

            parsed = parse_date(pub_date) if pub_date else None
            if parsed and not is_recent(parsed):
                continue

            items.append({
                "title": clean_html(entry.get("title", "")),
                "link": entry.get("link", ""),
                "published": parsed.strftime("%Y-%m-%d") if parsed else (entry.get("published", "")[:25]),
                "source": "Bing News",
                "summary": clean_html(entry.get("summary", ""))[:200],
                "date_obj": parsed,
            })
        return items
    except Exception as e:
        print(f"  [WARN] Bing News 失败 ({query[:30]}): {e}")
        return []


def search_game_media_rss():
    """直接抓取游戏垂直媒体的 RSS，时效性最高"""
    sources = [
        # 国内
        ("https://feedx.net/rss/gamelook.xml", "GameLook"),
        ("https://feedx.net/rss/gcores.xml", "机核"),
        ("http://www.yystv.cn/rss/feed", "游研社"),
        # 海外 (英文)
        ("https://www.pocketgamer.com/feed/", "Pocket Gamer"),
        ("https://www.gamesindustry.biz/feed", "GamesIndustry"),
    ]
    items = []
    for url, name in sources:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:4]:
                pub_date = entry.get("published_parsed", None) or entry.get("published", None)
                parsed = parse_date(pub_date) if pub_date else None
                if parsed and not is_recent(parsed):
                    continue
                items.append({
                    "title": clean_html(entry.get("title", "")),
                    "link": entry.get("link", ""),
                    "published": parsed.strftime("%Y-%m-%d") if parsed else "",
                    "source": name,
                    "summary": clean_html(entry.get("summary", ""))[:200],
                    "date_obj": parsed,
                })
        except Exception as e:
            print(f"  [WARN] 媒体 RSS {name} 失败: {e}")
    return items


# ── 搜索计划：每次生成动态查询词 ──────────────────

def build_search_queries():
    """根据当前日期生成时效性强的搜索词"""
    now = datetime.datetime.now()
    month_cn = f"{now.month}月"
    year_month = now.strftime("%Y年%m月")
    year_month_en = now.strftime("%B %Y")

    categories = {
        "🆕 手游新品上线 & 测试": [
            f"手游 公测 上线 {year_month}",
            f"手游 内测 预约 {month_cn}",
            f"手机游戏 新游 首发 {year_month}",
            f"mobile game release {year_month_en}",
            f"手游 TapTap 新品 {month_cn}",
        ],
        "📜 版号与政策动态": [
            f"游戏版号 审批 {year_month}",
            f"国家新闻出版署 网络游戏 {month_cn}",
            f"游戏监管 政策 {year_month}",
            f"游戏 未成年人 保护 {month_cn}",
        ],
        "🌏 出海 & 海外市场": [
            f"中国手游 出海 海外 {year_month}",
            f"Sensor Tower 手游 榜单 {month_cn}",
            f"手游 全球市场 收入 {year_month}",
            f"mobile game overseas China {year_month_en}",
        ],
        "🤖 AI 在游戏行业的影响": [
            f"AI 游戏 开发 {year_month}",
            f"人工智能 游戏 NPC {month_cn}",
            f"大模型 游戏 应用 {year_month}",
            f"AI game development {year_month_en}",
        ],
    }
    return categories


# ── 核心收集逻辑 ──────────────────────────────────

def collect_all_news():
    """多源 + 时效过滤 → 生成按日期降序排列的简报"""
    categories = build_search_queries()
    all_news = {}
    total_queries = sum(len(v) for v in categories.values())
    completed = 0

    for category, queries in categories.items():
        print(f"\n📌 {category}")
        category_news = []
        seen_titles = set()

        # 1. Google News 搜索
        for query in queries:
            results = search_google_news_rss(query, max_results=6)
            for item in results:
                key = item["title"][:80]
                if key not in seen_titles:
                    seen_titles.add(key)
                    category_news.append(item)
            completed += 1
            if completed % 5 == 0:
                print(f"  进度: {completed}/{total_queries}")

        # 2. Bing News 补充 (只用 1-2 个主查询)
        if queries:
            bing_results = search_bing_news(queries[0], max_results=4)
            for item in bing_results:
                key = item["title"][:80]
                if key not in seen_titles:
                    seen_titles.add(key)
                    category_news.append(item)

        # 按日期降序排列
        category_news.sort(
            key=lambda x: (x["date_obj"] or datetime.datetime.min),
            reverse=True
        )

        all_news[category] = category_news[:MAX_PER_CATEGORY]
        fresh = sum(1 for n in category_news if n["date_obj"] and is_recent(n["date_obj"], 7))
        print(f"  → 收集 {len(category_news)} 条，近7天 {fresh} 条")

    # 3. 行业垂直媒体补充
    print("\n📌 行业垂直媒体 RSS")
    media_news = search_game_media_rss()
    print(f"  → 收集 {len(media_news)} 条媒体资讯")
    # 分配到不同类别（基于标题关键词）
    for item in media_news:
        title = item["title"].lower()
        if any(kw in title for kw in ["出海", "海外", "全球", "global", "overseas", "international"]):
            target = "🌏 出海 & 海外市场"
        elif any(kw in title for kw in ["ai", "人工智能", "机器学习", "大模型", "llm", "gpt", "生成"]):
            target = "🤖 AI 在游戏行业的影响"
        elif any(kw in title for kw in ["版号", "政策", "监管", "审批", "法规", "未成年"]):
            target = "📜 版号与政策动态"
        else:
            target = "🆕 手游新品上线 & 测试"

        key = item["title"][:80]
        existing_titles = {n["title"][:80] for n in all_news.get(target, [])}
        if key not in existing_titles:
            all_news[target].append(item)
            all_news[target].sort(
                key=lambda x: (x["date_obj"] or datetime.datetime.min),
                reverse=True
            )
            all_news[target] = all_news[target][:MAX_PER_CATEGORY]

    return all_news


# ── 生成 HTML 邮件 ────────────────────────────────

def build_html_email(all_news):
    today_str = datetime.date.today().strftime("%Y年%m月%d日")
    cutoff_str = (datetime.date.today() - datetime.timedelta(days=MAX_AGE_DAYS)).strftime("%m月%d日")

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
            items_html = '<li style="color:#999;padding:8px 0;">暂无最新动态</li>'
        else:
            for item in items:
                pub = item.get("published", "")[:16]
                # 标注新旧
                badge = ""
                if item.get("date_obj"):
                    days_ago = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) -
                                (item["date_obj"].replace(tzinfo=None) if item["date_obj"].tzinfo else item["date_obj"]))
                    if days_ago.days <= 1:
                        badge = ' <span style="background:#ff4757;color:#fff;font-size:11px;padding:2px 6px;border-radius:4px;">NEW</span>'
                    elif days_ago.days <= 7:
                        badge = ' <span style="background:#ffa502;color:#fff;font-size:11px;padding:2px 6px;border-radius:4px;">本周</span>'

                items_html += f"""
                <li style="margin-bottom:14px;padding-bottom:14px;border-bottom:1px solid #f2f2f2;">
                    <a href="{item['link']}" style="color:#333;text-decoration:none;font-weight:600;font-size:15px;line-height:1.6;" target="_blank">
                        {item['title']}{badge}
                    </a>
                    <div style="display:flex;align-items:center;gap:8px;margin-top:6px;">
                        <span style="color:{color};font-size:12px;font-weight:500;">📰 {item['source']}</span>
                        <span style="color:#999;font-size:11px;">📅 {pub}</span>
                    </div>
                    <div style="color:#666;font-size:13px;margin-top:6px;line-height:1.6;">
                        {item.get('summary', '')}
                    </div>
                </li>
                """

        sections_html += f"""
        <div style="margin-bottom:28px;">
            <h2 style="color:{color};font-size:18px;margin:0 0 14px 0;padding-bottom:10px;border-bottom:2px solid {color};">
                {category} <span style="font-weight:400;color:#999;">({len(items)}条)</span>
            </h2>
            <ul style="list-style:none;padding:0;margin:0;">
                {items_html}
            </ul>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f5f5f5;padding:20px 0;">
        <tr>
            <td align="center">
                <table width="640" cellpadding="0" cellspacing="0" style="background-color:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 16px rgba(0,0,0,0.08);">
                    <tr>
                        <td style="background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);padding:36px 40px;text-align:center;">
                            <h1 style="color:#fff;font-size:26px;margin:0 0 6px 0;">🎮 手游行业资讯简报</h1>
                            <p style="color:rgba(255,255,255,0.7);font-size:13px;margin:0;">{today_str} · 近{MAX_AGE_DAYS}天动态 · 四大方向 · 多源聚合</p>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:32px 40px 16px;">
                            <p style="color:#888;font-size:12px;margin:0 0 20px 0;background:#f0f4ff;padding:10px 16px;border-radius:8px;">
                                🔍 搜索范围：{cutoff_str} 至今 · 数据来源：Google News / Bing News / 游戏行业媒体 RSS · 由 GitHub Actions 云端自动生成
                            </p>
                            {sections_html}
                        </td>
                    </tr>
                    <tr>
                        <td style="background-color:#fafafa;padding:20px 40px;text-align:center;border-top:1px solid #eee;">
                            <p style="color:#aaa;font-size:11px;margin:0;">
                                📬 每日自动推送 · 云端运行不依赖本地<br>
                                <a href="https://github.com/zhiwen20000223-coder/game-news-digest" style="color:#667eea;">github.com/zhiwen20000223-coder/game-news-digest</a>
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
    today_str = datetime.date.today().strftime("%Y%m%d")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎮 手游行业资讯简报 | {today_str}"
    msg["From"] = formataddr(("游戏资讯助手", SENDER))
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


def main():
    print("=" * 55)
    print("🎮 手游行业资讯简报 v2 — 多源聚合")
    print(f"📅 日期: {datetime.date.today()}")
    print(f"⏱️  时效范围: 近 {MAX_AGE_DAYS} 天")
    print(f"📧 收件人: {RECEIVER}")
    print("=" * 55)

    print("\n🔍 开始多源搜索...")
    all_news = collect_all_news()

    total = sum(len(v) for v in all_news.values())
    print(f"\n📊 总计收集: {total} 条资讯")

    print("\n📝 生成 HTML 邮件...")
    html_content = build_html_email(all_news)

    print("📧 发送邮件...")
    success = send_email(html_content)

    if success:
        print("\n🎉 任务完成!")
    else:
        print("\n⚠️ 任务失败")
        exit(1)


if __name__ == "__main__":
    main()
