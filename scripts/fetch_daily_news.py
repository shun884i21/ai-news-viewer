#!/usr/bin/env python3
"""毎朝のAIニュースを生成して news.json を更新する。

GitHub Actions のクラウド上で動く想定（PC不要）。
1. 5媒体のRSSから直近のAI関連記事を集める
2. Claude API に渡して「最新5本＋日本語要約＋今後の展望」をJSONで作らせる
3. news.json の先頭に当日分を追加して書き出す（同日があれば置換、sampleは削除）

必要な環境変数:
  ANTHROPIC_API_KEY  … Claude API キー（GitHub Secrets で渡す）
  NEWS_MODEL         … 任意。使用モデル（既定: claude-sonnet-4-6）
"""

import json
import os
import sys
import datetime
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

import anthropic

JST = datetime.timezone(datetime.timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_PATH = os.path.join(ROOT, "news.json")
MODEL = os.environ.get("NEWS_MODEL", "claude-sonnet-4-6")

# 5媒体のRSS。BloombergはRSSが弱いのでGoogleニュース経由で site 絞り込み。
FEEDS = [
    ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("VentureBeat", "https://venturebeat.com/feed/"),
    ("MIT Technology Review", "https://www.technologyreview.com/feed/"),
    ("Bloomberg", "https://news.google.com/rss/search?q=AI%20site:bloomberg.com%20when:3d&hl=en-US&gl=US&ceid=US:en"),
]

# AI関連かどうかのゆるいフィルタ（The Verge等は全ジャンル混在のため）
AI_HINTS = ("ai", "artificial intelligence", "openai", "anthropic", "claude", "gpt",
            "llm", "nvidia", "deepmind", "gemini", "model", "chatbot", "machine learning",
            "agent", "chip", "data center", "datacenter")


def strip_html(text):
    out, depth = [], 0
    for ch in text:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out).strip()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ai-news-bot)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def parse_feed(source, url, days=4):
    """RSS/Atom を雑にパースして直近 days 日のエントリを返す。"""
    cutoff = datetime.datetime.now(JST) - datetime.timedelta(days=days)
    items = []
    try:
        raw = fetch(url)
    except Exception as e:
        print(f"[warn] fetch失敗 {source}: {e}", file=sys.stderr)
        return items
    try:
        root = ET.fromstring(raw)
    except Exception as e:
        print(f"[warn] parse失敗 {source}: {e}", file=sys.stderr)
        return items

    # RSS(channel/item) と Atom(entry) の両対応
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall(".//item") or root.findall(".//a:entry", ns)
    for e in entries:
        def g(*tags):
            for t in tags:
                el = e.find(t) if not t.startswith("a:") else e.find(t, ns)
                if el is not None and (el.text or el.get("href")):
                    return el.text or el.get("href")
            return ""
        title = strip_html(g("title", "a:title"))
        link = g("link", "a:link")
        if isinstance(link, str) and not link and e.find("a:link", ns) is not None:
            link = e.find("a:link", ns).get("href", "")
        desc = strip_html(g("description", "a:summary", "a:content"))
        pub = g("pubDate", "a:published", "a:updated")
        if not title or not link:
            continue
        # ざっくり日付パース（失敗したら採用＝鮮度はモデルに最終判断させる）
        dt = None
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                    "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                dt = datetime.datetime.strptime(pub.strip(), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                break
            except Exception:
                continue
        if dt is not None and dt < cutoff:
            continue
        hay = (title + " " + desc).lower()
        if not any(h in hay for h in AI_HINTS):
            continue
        items.append({
            "source": source,
            "title": title,
            "url": link.strip(),
            "publishedAt": (dt.astimezone(JST).strftime("%Y-%m-%d") if dt else ""),
            "snippet": desc[:400],
        })
    return items[:12]


def gather():
    pool = []
    for source, url in FEEDS:
        pool.extend(parse_feed(source, url))
    print(f"[info] 収集 {len(pool)} 件", file=sys.stderr)
    return pool


def curate(pool, today):
    """Claude API に候補を渡して 5本＋展望のJSONを作らせる。"""
    client = anthropic.Anthropic()
    catalog = "\n".join(
        f"- [{p['source']}] {p['title']} ({p['publishedAt'] or '日付不明'})\n  url: {p['url']}\n  snippet: {p['snippet']}"
        for p in pool
    )
    prompt = f"""あなたはAIニュースのキュレーターです。本日は {today} です。
以下はThe Verge / TechCrunch / VentureBeat / MIT Technology Review / Bloomberg のRSSから集めた候補記事です。
この中から、本日付近で最も重要なAIニュースを最大5本選び、日本語でまとめてください。

# 選定方針
- 実行日（{today}）付近の最新記事を優先。古い記事・まとめ記事・薄い記事は避ける。
- 業界横断（新モデル/企業・資金/規制・政策/研究 等）でインパクト順。媒体が偏らないよう努める。
- 候補が乏しい場合は無理に5本にせず、拾える本数だけにする。

# 各記事の要約
- 日本語4〜6文・180〜280字。何が起きたかだけでなく背景・なぜ重要か・影響まで含め、読まなくても要点が掴める詳しさにする。
- snippetを基に書く。事実を捏造しない。snippetで足りない部分は一般化した表現に留める。

# 出力（厳密なJSONのみ。前後に文章を付けない）
{{
  "items": [
    {{"title": "日本語見出し", "summary": "日本語要約", "url": "元記事URL", "source": "媒体名", "publishedAt": "YYYY-MM-DD"}}
  ],
  "outlook": "5本を踏まえた今後の展望（日本語・数文）",
  "note": "5本未満や注意点があれば日本語で。無ければ空文字"
}}

候補記事:
{catalog}
"""
    msg = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    # ```json フェンスが付いた場合に備えて剥がす
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    data = json.loads(text)
    if not data.get("items"):
        raise SystemExit("[error] モデルが記事を返さなかった")
    return data


def update_news_json(day_entry, today):
    try:
        with open(NEWS_PATH, encoding="utf-8") as f:
            news = json.load(f)
    except Exception:
        news = {"updatedAt": "", "days": []}
    days = [d for d in news.get("days", []) if d.get("date") != today and not d.get("sample")]
    days.insert(0, day_entry)
    days.sort(key=lambda d: d.get("date", ""), reverse=True)
    news["days"] = days
    news["updatedAt"] = datetime.datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    with open(NEWS_PATH, "w", encoding="utf-8") as f:
        json.dump(news, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"[info] news.json 更新: {today} / {len(day_entry['items'])}本", file=sys.stderr)


def main():
    today = datetime.datetime.now(JST).strftime("%Y-%m-%d")
    pool = gather()
    if not pool:
        raise SystemExit("[error] 候補記事が0件。RSS取得に失敗した可能性。")
    data = curate(pool, today)
    day_entry = {
        "date": today,
        "items": data["items"][:5],
        "outlook": data.get("outlook", ""),
    }
    if data.get("note"):
        day_entry["note"] = data["note"]
    update_news_json(day_entry, today)


if __name__ == "__main__":
    main()
