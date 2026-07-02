#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日手游行业资讯简报 v4
运行环境: GitHub Actions
策略: 多源搜索 + 多地区回退 + 日期过滤 → HTML 邮件 → QQ邮箱 SMTP
v4 新增: ⭐ 黑马新星游戏板块 (Google Play / TapTap / 微信小游戏 / Steam)
"""

import os
import re
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

MAX_AGE_DAYS = 30
MAX_PER_CATEGORY = 8
MAX_RISING_STARS = 8   # 黑马板块最大条数

# 浏览器 UA，防止被反爬
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
}

# ── 工具函数 ──────────────────────────────────────

def clean_html(text):
    return re.sub(r"<[^>]+>", "", text).strip()


def parse_date(date_str):
    """解析各种日期格式，返回 aware datetime"""
    if not date_str:
        return None

    # feedparser 的 struct_time
    if hasattr(date_str, "tm_year"):
        try:
            ts = time.mktime(date_str)
            return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        except Exception:
            pass

    # 字符串格式
    if isinstance(date_str, str):
        date_str = date_str.strip()
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
                dt = datetime.datetime.strptime(date_str, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                return dt
            except (ValueError, TypeError):
                continue

    return None


def is_recent(date_obj, max_days=MAX_AGE_DAYS):
    if date_obj is None:
        return False
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=max_days)
    if date_obj.tzinfo is None:
        date_obj = date_obj.replace(tzinfo=datetime.timezone.utc)
    return date_obj >= cutoff


def fetch_feed(url, timeout=15):
    """用 requests 抓 RSS，再交给 feedparser 解析"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return feedparser.parse(resp.content)
    except requests.RequestException as e:
        print(f"    HTTP 错误: {e}")
        return None
    except Exception as e:
        print(f"    解析错误: {e}")
        return None


def make_item(title, link, published, source, summary, date_obj):
    return {
        "title": title,
        "link": link,
        "published": published,
        "source": source,
        "summary": summary,
        "date_obj": date_obj,
        "reviews": [],   # 附加测评列表
    }


# ── Google News 搜索（多地区回退） ────────────────

def search_google_news(query, max_results=6):
    """
    Google News RSS，多地区回退。
    回退顺序: CN → US → 无限制
    """
    region_configs = [
        ("zh-CN", "CN", "CN"),
        ("zh-CN", "US", "US"),
        ("zh-CN", None, "无限制"),
    ]

    if "when:" not in query:
        query += " when:30d"

    for hl, gl, label in region_configs:
        if gl:
            url = f"https://news.google.com/rss/search?q={quote(query)}&hl={hl}&gl={gl}&ceid={gl}:{hl}"
        else:
            url = f"https://news.google.com/rss/search?q={quote(query)}&hl={hl}"

        feed = fetch_feed(url)
        if feed is None:
            print(f"    [{label}] 请求失败，尝试下一个地区...")
            continue

        n = len(feed.entries)
        if n == 0:
            print(f"    [{label}] 0 条结果，尝试下一个地区...")
            continue

        items = []
        for entry in feed.entries[:max_results]:
            pub_date = entry.get("published_parsed") or entry.get("published")
            parsed = parse_date(pub_date)
            if parsed and not is_recent(parsed):
                continue

            items.append(make_item(
                title=clean_html(entry.get("title", "")),
                link=entry.get("link", ""),
                published=parsed.strftime("%Y-%m-%d") if parsed else str(entry.get("published", ""))[:25],
                source=entry.get("source", {}).get("title", "Google News"),
                summary=clean_html(entry.get("summary", ""))[:200],
                date_obj=parsed,
            ))

        if items:
            print(f"    [{label}] ✅ {n} 条原始，过滤后 {len(items)} 条")
            return items
        else:
            print(f"    [{label}] {n} 条原始，全部被日期过滤")

    print(f"    ⚠️ 所有地区均无有效结果")
    return []


# ── 垂直媒体 RSS ──────────────────────────────────

def search_game_media():
    """已验证可用的游戏媒体 RSS"""
    sources = [
        ("http://www.yystv.cn/rss/feed", "游研社"),
        ("https://www.gcores.com/rss", "机核"),
    ]
    items = []
    for url, name in sources:
        feed = fetch_feed(url)
        if feed is None or len(feed.entries) == 0:
            print(f"    {name}: 无数据 (HTTP 错误或空)")
            continue

        count = 0
        for entry in feed.entries[:5]:
            pub_date = entry.get("published_parsed") or entry.get("published")
            parsed = parse_date(pub_date)
            if parsed and not is_recent(parsed):
                continue
            items.append(make_item(
                title=clean_html(entry.get("title", "")),
                link=entry.get("link", ""),
                published=parsed.strftime("%Y-%m-%d") if parsed else "",
                source=name,
                summary=clean_html(entry.get("summary", ""))[:200],
                date_obj=parsed,
            ))
            count += 1
        print(f"    {name}: {len(feed.entries)} 条原始，收录 {count} 条")

    return items


# ── 备用：网页抓取兜底 ────────────────────────────

def search_web_scrape(query):
    """用 Bing 网页搜索做最后兜底"""
    url = f"https://www.bing.com/search?q={quote(query)}&filters=ex1:\"ez1\"&form=QBLH"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return []
        items = []
        pattern = r'<h2><a\s+href="([^"]+)"[^>]*>([^<]+)</a></h2>'
        matches = re.findall(pattern, resp.text)
        for link, title in matches[:5]:
            title = clean_html(title)
            if not title or len(title) < 5:
                continue
            items.append(make_item(
                title=title,
                link=link,
                published=datetime.date.today().strftime("%Y-%m-%d"),
                source="Bing 搜索",
                summary="",
                date_obj=datetime.datetime.now(datetime.timezone.utc),
            ))
        return items
    except Exception as e:
        print(f"    网页抓取失败: {e}")
        return []


# ── ⭐ 黑马新星游戏专属搜索 ────────────────────────

def fetch_rising_star_reviews(game_title, max_results=2):
    """
    根据游戏名称搜索测评/体验报告，最多返回 max_results 条。
    """
    review_query = f'"{game_title}" 测评 OR 体验报告 OR 首测 OR 深度测试 OR review when:30d'
    results = search_google_news(review_query, max_results=max_results)
    reviews = []
    for r in results:
        if r["title"] != game_title:  # 排除和游戏同名的重复标题
            reviews.append({
                "title": r["title"],
                "link": r["link"],
                "source": r["source"],
                "published": r["published"],
            })
    return reviews[:max_results]


def collect_rising_stars():
    """
    专项搜索黑马/低曝光高口碑新星游戏，涵盖四大平台。
    每个平台 2~4 条，然后尝试附上测评报道。
    """
    print(f"\n{'='*50}")
    print("📌 ⭐ 黑马新星游戏（低曝光·高口碑）")

    now = datetime.datetime.now()
    month_cn = f"{now.month}月"
    year_month = now.strftime("%Y年%m月")

    platform_queries = {
        "Google Play": [
            f"Google Play 黑马游戏 好评 {year_month}",
            f"Google Play 新游 逆袭 口碑 {month_cn}",
            f"安卓 手游 小众 爆款 {year_month}",
            f"Google Play hidden gem mobile game {now.strftime('%B %Y')}",
        ],
        "TapTap": [
            f"TapTap 黑马 高分 新游 {year_month}",
            f"TapTap 好评新游 推荐 {month_cn}",
            f"TapTap 宝藏游戏 评分 {year_month}",
            f"TapTap 热度上升 低曝光 {month_cn}",
        ],
        "微信小游戏": [
            f"微信小游戏 黑马 爆款 {year_month}",
            f"微信小游戏 好评 低调 {month_cn}",
            f"微信小游戏 月活 增长 惊喜 {year_month}",
        ],
        "Steam": [
            f"Steam 独立游戏 黑马 好评如潮 {year_month}",
            f"Steam 新品 小众 逆袭 评分 {month_cn}",
            f"Steam hidden gem indie {now.strftime('%B %Y')}",
            f"Steam 低曝光 高分 推荐 {year_month}",
        ],
    }

    all_items = []
    seen_titles = set()

    for platform, queries in platform_queries.items():
        print(f"  [{platform}]")
        platform_items = []
        for query in queries:
            results = search_google_news(query, max_results=4)
            for item in results:
                key = item["title"][:60]
                if key not in seen_titles:
                    seen_titles.add(key)
                    item["platform_tag"] = platform
                    platform_items.append(item)
            if len(platform_items) >= 3:
                break

        print(f"    → {platform} 收到 {len(platform_items)} 条")
        all_items.extend(platform_items[:3])

    # 按日期排序
    all_items.sort(
        key=lambda x: (x["date_obj"] or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)),
        reverse=True
    )
    all_items = all_items[:MAX_RISING_STARS]

    # 为每个游戏追加测评报道
    print(f"  🔍 搜寻测评报道...")
    for item in all_items:
        # 尝试从标题中提取游戏名（取前30字符）
        game_hint = item["title"][:30].split("：")[0].split(":")[0].split("—")[0].strip()
        reviews = fetch_rising_star_reviews(game_hint, max_results=2)
        item["reviews"] = reviews
        if reviews:
            print(f"    📝 {game_hint[:20]}... → {len(reviews)} 条测评")

    print(f"  → 共收录 {len(all_items)} 条黑马新星")
    return all_items


# ── 搜索计划 ──────────────────────────────────────

def build_search_queries():
    now = datetime.datetime.now()
    month_cn = f"{now.month}月"
    year_month = now.strftime("%Y年%m月")

    return {
        "🆕 手游新品上线 & 测试": [
            f"手游 公测 上线 {year_month}",
            f"手游 内测 预约 {month_cn}",
            f"手机游戏 新游 {year_month}",
            f"手游 TapTap 新品 {month_cn}",
        ],
        "📜 版号与政策动态": [
            f"游戏版号 审批 {year_month}",
            f"国家新闻出版署 网络游戏 {month_cn}",
            f"游戏监管 政策 {year_month}",
        ],
        "🌏 出海 & 海外市场": [
            f"中国手游 出海 {year_month}",
            f"Sensor Tower 手游 榜单 {month_cn}",
            f"手游 全球市场 收入 {year_month}",
        ],
        "🤖 AI 在游戏行业的影响": [
            f"AI 游戏 开发 {year_month}",
            f"人工智能 游戏 NPC {month_cn}",
            f"大模型 游戏 应用 {year_month}",
        ],
    }


# ── 核心收集 ──────────────────────────────────────

def collect_all_news():
    categories = build_search_queries()
    all_news = {}
    total_queries = sum(len(v) for v in categories.values())
    completed = 0

    for category, queries in categories.items():
        print(f"\n{'='*50}")
        print(f"📌 {category}")
        category_news = []
        seen_titles = set()

        for query in queries:
            results = search_google_news(query, max_results=5)
            for item in results:
                key = item["title"][:80]
                if key not in seen_titles:
                    seen_titles.add(key)
                    category_news.append(item)
            completed += 1
            if completed % 4 == 0:
                print(f"  [进度 {completed}/{total_queries}]")

        # 兜底
        google_count = len(category_news)
        if google_count == 0 and queries:
            print(f"  ⚠️ Google 零结果，启用 Bing 网页抓取兜底...")
            for query in queries[:2]:
                fallback = search_web_scrape(query)
                for item in fallback:
                    key = item["title"][:80]
                    if key not in seen_titles:
                        seen_titles.add(key)
                        category_news.append(item)

        category_news.sort(
            key=lambda x: (x["date_obj"] or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)),
            reverse=True
        )
        all_news[category] = category_news[:MAX_PER_CATEGORY]

        fresh7 = sum(1 for n in category_news if n["date_obj"] and is_recent(n["date_obj"], 7))
        fresh1 = sum(1 for n in category_news if n["date_obj"] and is_recent(n["date_obj"], 1))
        print(f"  → 共 {len(category_news)} 条 (Google:{google_count}), 24h:{fresh1}, 7d:{fresh7}")

    # 垂直媒体补充
    print(f"\n{'='*50}")
    print("📌 行业垂直媒体 RSS")
    media_news = search_game_media()
    print(f"  → 收录 {len(media_news)} 条")

    for item in media_news:
        title = item["title"].lower()
        if any(kw in title for kw in ["出海", "海外", "全球", "global", "overseas"]):
            target = "🌏 出海 & 海外市场"
        elif any(kw in title for kw in ["ai", "人工智能", "大模型", "llm", "gpt", "生成"]):
            target = "🤖 AI 在游戏行业的影响"
        elif any(kw in title for kw in ["版号", "政策", "监管", "审批", "未成年"]):
            target = "📜 版号与政策动态"
        else:
            target = "🆕 手游新品上线 & 测试"

        key = item["title"][:80]
        existing = {n["title"][:80] for n in all_news.get(target, [])}
        if key not in existing:
            all_news[target].append(item)
            all_news[target].sort(
                key=lambda x: (x["date_obj"] or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)),
                reverse=True
            )
            all_news[target] = all_news[target][:MAX_PER_CATEGORY]

    return all_news


# ── HTML 邮件 ─────────────────────────────────────

def build_badge_html(item, color):
    badge = ""
    if item.get("date_obj"):
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            item_dt = item["date_obj"]
            if item_dt.tzinfo is None:
                item_dt = item_dt.replace(tzinfo=datetime.timezone.utc)
            days_ago = (now - item_dt).days
            if days_ago <= 1:
                badge = ' <span style="background:#ff4757;color:#fff;font-size:11px;padding:2px 6px;border-radius:3px;">NEW</span>'
            elif days_ago <= 7:
                badge = ' <span style="background:#ffa502;color:#fff;font-size:11px;padding:2px 6px;border-radius:3px;">本周</span>'
        except Exception:
            pass
    return badge


def build_news_item_html(item, color):
    pub = item.get("published", "")[:16]
    badge = build_badge_html(item, color)
    summary_text = item.get("summary", "")
    summary_html = ""
    if summary_text:
        summary_html = f'<div style="color:#666;font-size:13px;margin-top:5px;line-height:1.6;">{summary_text}</div>'

    return f"""
    <li style="margin-bottom:14px;padding-bottom:14px;border-bottom:1px solid #f0f0f0;">
        <a href="{item['link']}" style="color:#2c3e50;text-decoration:none;font-weight:600;font-size:15px;line-height:1.6;" target="_blank">
            {item['title']}{badge}
        </a>
        <div style="display:flex;align-items:center;gap:8px;margin-top:5px;">
            <span style="color:{color};font-size:12px;font-weight:500;">📰 {item['source']}</span>
            <span style="color:#999;font-size:11px;">📅 {pub}</span>
        </div>
        {summary_html}
    </li>
    """


def build_rising_star_item_html(item):
    """黑马游戏条目，带平台标签 + 测评子列表"""
    pub = item.get("published", "")[:16]
    badge = build_badge_html(item, "#F4A300")
    platform = item.get("platform_tag", "")
    summary_text = item.get("summary", "")

    platform_colors = {
        "Google Play": "#01875f",
        "TapTap": "#e6462e",
        "微信小游戏": "#07C160",
        "Steam": "#1b2838",
    }
    pt_color = platform_colors.get(platform, "#888")
    pt_badge = f'<span style="background:{pt_color};color:#fff;font-size:11px;padding:2px 7px;border-radius:3px;margin-left:6px;">{platform}</span>' if platform else ""

    summary_html = ""
    if summary_text:
        summary_html = f'<div style="color:#666;font-size:13px;margin-top:5px;line-height:1.6;">{summary_text[:180]}</div>'

    # 测评子列表
    reviews = item.get("reviews", [])
    reviews_html = ""
    if reviews:
        review_items = ""
        for r in reviews:
            review_items += f"""
            <li style="padding:4px 0;border-bottom:1px dotted #f5e6c8;">
                <a href="{r['link']}" style="color:#8B6914;font-size:12px;text-decoration:none;" target="_blank">
                    📝 {r['title'][:70]}
                </a>
                <span style="color:#bbb;font-size:11px;margin-left:6px;">{r['source']} · {r['published'][:10]}</span>
            </li>"""
        reviews_html = f"""
        <div style="background:#fffbf0;border-left:3px solid #F4A300;padding:8px 12px;margin-top:8px;border-radius:0 6px 6px 0;">
            <div style="font-size:12px;color:#8B6914;font-weight:600;margin-bottom:4px;">🔍 相关测评报道</div>
            <ul style="list-style:none;padding:0;margin:0;">{review_items}</ul>
        </div>"""

    return f"""
    <li style="margin-bottom:18px;padding-bottom:18px;border-bottom:1px solid #f5e6c8;">
        <a href="{item['link']}" style="color:#2c3e50;text-decoration:none;font-weight:600;font-size:15px;line-height:1.6;" target="_blank">
            {item['title']}{badge}
        </a>{pt_badge}
        <div style="display:flex;align-items:center;gap:8px;margin-top:5px;">
            <span style="color:#F4A300;font-size:12px;font-weight:500;">📰 {item['source']}</span>
            <span style="color:#999;font-size:11px;">📅 {pub}</span>
        </div>
        {summary_html}
        {reviews_html}
    </li>
    """


def build_html_email(all_news, rising_stars):
    today_str = datetime.date.today().strftime("%Y年%m月%d日")

    colors = {
        "🆕 手游新品上线 & 测试": "#FF6B35",
        "📜 版号与政策动态": "#4ECDC4",
        "🌏 出海 & 海外市场": "#1A535C",
        "🤖 AI 在游戏行业的影响": "#7B2DFF",
    }

    sections_html = ""
    total_items = 0

    # 四大常规板块
    for category, items in all_news.items():
        color = colors.get(category, "#333")
        if not items:
            items_html = '<li style="color:#999;padding:8px 0;font-size:13px;">😴 本时段暂无相关动态</li>'
        else:
            items_html = "".join(build_news_item_html(item, color) for item in items)
            total_items += len(items)

        sections_html += f"""
        <div style="margin-bottom:28px;">
            <h2 style="color:{color};font-size:18px;margin:0 0 12px 0;padding-bottom:10px;border-bottom:2px solid {color};">
                {category} <span style="font-weight:400;color:#999;font-size:14px;">({len(items)}条)</span>
            </h2>
            <ul style="list-style:none;padding:0;margin:0;">
                {items_html}
            </ul>
        </div>
        """

    # ⭐ 黑马新星游戏板块
    rising_color = "#F4A300"
    if not rising_stars:
        rising_items_html = '<li style="color:#999;padding:8px 0;font-size:13px;">😴 本时段暂未发现明显黑马产品</li>'
    else:
        rising_items_html = "".join(build_rising_star_item_html(item) for item in rising_stars)
        total_items += len(rising_stars)

    platform_tags = """
        <div style="display:inline-flex;gap:6px;flex-wrap:wrap;margin-bottom:12px;">
            <span style="background:#01875f;color:#fff;font-size:11px;padding:2px 8px;border-radius:12px;">Google Play</span>
            <span style="background:#e6462e;color:#fff;font-size:11px;padding:2px 8px;border-radius:12px;">TapTap</span>
            <span style="background:#07C160;color:#fff;font-size:11px;padding:2px 8px;border-radius:12px;">微信小游戏</span>
            <span style="background:#1b2838;color:#fff;font-size:11px;padding:2px 8px;border-radius:12px;">Steam</span>
        </div>
    """

    sections_html += f"""
    <div style="margin-bottom:28px;background:#fffdf5;border:1px solid #ffe4a0;border-radius:10px;padding:20px 24px;">
        <h2 style="color:{rising_color};font-size:18px;margin:0 0 8px 0;padding-bottom:10px;border-bottom:2px solid {rising_color};">
            ⭐ 黑马新星游戏
            <span style="font-weight:400;color:#999;font-size:14px;">({len(rising_stars)}条)</span>
        </h2>
        <p style="color:#8B6914;font-size:12px;margin:0 0 10px 0;line-height:1.6;">
            🔍 低曝光度 · 高市场反应 · 覆盖四大平台 · 附测评报道
        </p>
        {platform_tags}
        <ul style="list-style:none;padding:0;margin:0;">
            {rising_items_html}
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
                <table width="660" cellpadding="0" cellspacing="0" style="background-color:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 16px rgba(0,0,0,0.08);">
                    <tr>
                        <td style="background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);padding:36px 40px;text-align:center;">
                            <h1 style="color:#fff;font-size:26px;margin:0 0 6px 0;">🎮 手游行业资讯简报</h1>
                            <p style="color:rgba(255,255,255,0.7);font-size:13px;margin:0;">{today_str} · 共 {total_items} 条资讯 · 五大板块 · 云端自动生成</p>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:32px 40px 16px;">
                            <p style="color:#888;font-size:12px;margin:0 0 24px 0;background:#f0f4ff;padding:10px 16px;border-radius:8px;">
                                🔍 数据来源：Google News (多地区) · 游研社/机核 RSS · 黑马板块覆盖 Google Play / TapTap / 微信小游戏 / Steam<br>
                                ⏱️ 仅保留近 {MAX_AGE_DAYS} 天资讯 · GitHub Actions 云端自动运行
                            </p>
                            {sections_html}
                        </td>
                    </tr>
                    <tr>
                        <td style="background-color:#fafafa;padding:20px 40px;text-align:center;border-top:1px solid #eee;">
                            <p style="color:#aaa;font-size:11px;margin:0;">
                                📬 每日 9:30 自动推送 · 不依赖本地电脑<br>
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
    today_str = datetime.date.today().strftime("%m%d")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎮 手游行业资讯简报 | {today_str}"
    msg["From"] = formataddr(("游戏资讯助手", SENDER))
    msg["To"] = RECEIVER
    msg.attach(MIMEText(html_content, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(SENDER, PASSWORD)
            server.sendmail(SENDER, [RECEIVER], msg.as_string())
        print(f"\n✅ 邮件发送成功 → {RECEIVER}")
        return True
    except Exception as e:
        print(f"\n❌ 邮件发送失败: {e}")
        return False


# ── 主流程 ────────────────────────────────────────

def main():
    print("=" * 55)
    print("🎮 手游行业资讯简报 v4 — 五大板块 + 黑马新星")
    print(f"📅 {datetime.date.today()}")
    print(f"⏱️  时效: 近 {MAX_AGE_DAYS} 天")
    print(f"📧 收件人: {RECEIVER}")
    print("=" * 55)

    print("\n🔍 [1/2] 开始搜索常规四大板块...")
    all_news = collect_all_news()

    print("\n⭐ [2/2] 搜索黑马新星游戏...")
    rising_stars = collect_rising_stars()

    total = sum(len(v) for v in all_news.values()) + len(rising_stars)
    print(f"\n{'='*50}")
    print(f"📊 总计: {total} 条")
    for cat, items in all_news.items():
        print(f"  {cat}: {len(items)} 条")
    print(f"  ⭐ 黑马新星游戏: {len(rising_stars)} 条")

    if total == 0:
        print("\n⚠️ 所有来源均无结果，但仍然发送邮件（标注为空）")

    print("\n📝 生成 HTML...")
    html = build_html_email(all_news, rising_stars)

    print("📧 发送...")
    ok = send_email(html)
    if not ok:
        exit(1)
    print("\n🎉 完成!")


if __name__ == "__main__":
    main()
