#!/usr/bin/env python3
"""毎朝のAIニュースを生成して news.json を更新する。

GitHub Actions のクラウド上で動く想定（PC不要）。
1. 10媒体のRSSから直近のAI関連記事を集める
2. Claudeに渡して「最新5本＋日本語要約＋今後の展望」をJSONで作らせる
3. news.json の先頭に当日分を追加して書き出す（同日があれば置換、sampleは削除）

生成バックエンドは2系統（メルマガ図解バッチと同方式）:
  - CLAUDE_CODE_OAUTH_TOKEN があれば Claude Code CLI（サブスク枠・追加費用ゼロ）
  - 無ければ / CLIが失敗したら Anthropic API（従量課金）にフォールバック

必要な環境変数:
  CLAUDE_CODE_OAUTH_TOKEN … Claude Codeの長期トークン（`claude setup-token`で発行）
  ANTHROPIC_API_KEY       … Claude API キー（フォールバック用）
  NEWS_MODEL              … 任意。API時のモデル（既定: claude-sonnet-4-6）
  NEWS_CLI_MODEL          … 任意。CLI時のモデル（既定: sonnet）
  NEWS_BACKEND            … 任意。"cli"/"api" を強制指定（既定: 自動判定）
"""

import json
import os
import shutil
import subprocess
import sys
import datetime
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

JST = datetime.timezone(datetime.timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_PATH = os.path.join(ROOT, "news.json")
MODEL = os.environ.get("NEWS_MODEL", "claude-sonnet-4-6")
CLI_MODEL = os.environ.get("NEWS_CLI_MODEL", "sonnet")

# 主要テックメディアのRSS。BloombergとReutersはRSSが弱い/無いのでGoogleニュース経由で site 絞り込み。
# 毎日5本を安定して確保するため候補元を広く取る（重複除外後も5本残るように）。
FEEDS = [
    ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("VentureBeat", "https://venturebeat.com/feed/"),
    ("MIT Technology Review", "https://www.technologyreview.com/feed/"),
    ("Bloomberg", "https://news.google.com/rss/search?q=AI%20site:bloomberg.com%20when:3d&hl=en-US&gl=US&ceid=US:en"),
    ("Ars Technica", "https://arstechnica.com/ai/feed/"),
    ("The Register", "https://www.theregister.com/headlines.atom"),
    ("Wired", "https://www.wired.com/feed/tag/ai/latest/rss"),
    ("ZDNET", "https://www.zdnet.com/topic/artificial-intelligence/rss.xml"),
    ("Reuters", "https://news.google.com/rss/search?q=AI%20site:reuters.com%20when:3d&hl=en-US&gl=US&ceid=US:en"),
]

# AI関連かどうかのゆるいフィルタ（The Verge等は全ジャンル混在のため）
AI_HINTS = ("ai", "artificial intelligence", "openai", "anthropic", "claude", "gpt",
            "llm", "nvidia", "deepmind", "gemini", "model", "chatbot", "machine learning",
            "agent", "chip", "data center", "datacenter")


def norm_url(u):
    """URLを緩く正規化して重複判定に使う（scheme/www/クエリ/末尾スラッシュを無視）。"""
    u = (u or "").strip().lower()
    if not u:
        return ""
    for pre in ("https://", "http://"):
        if u.startswith(pre):
            u = u[len(pre):]
            break
    if u.startswith("www."):
        u = u[4:]
    u = u.split("#", 1)[0].split("?", 1)[0]
    return u.rstrip("/")


def published_history(days_back=7):
    """過去に配信済みの記事を返す。
    - seen_urls: 全期間の既出URL(正規化)の集合 → プールの機械的な重複除外に使う
    - recent: 直近 days_back 日ぶんの (date, title) → モデルへの「再掲禁止リスト」に使う
    """
    try:
        with open(NEWS_PATH, encoding="utf-8") as f:
            news = json.load(f)
    except Exception:
        return set(), []
    seen_urls = set()
    for d in news.get("days", []):
        if d.get("sample"):
            continue
        for it in d.get("items", []):
            u = norm_url(it.get("url", ""))
            if u:
                seen_urls.add(u)
    recent = []
    ordered = sorted((x for x in news.get("days", []) if not x.get("sample")),
                     key=lambda x: x.get("date", ""), reverse=True)
    for d in ordered[:days_back]:
        for it in d.get("items", []):
            recent.append((d.get("date", ""), it.get("title", "")))
    return seen_urls, recent


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
            "snippet": desc[:300],
        })
    return items[:8]


def gather():
    pool = []
    for source, url in FEEDS:
        pool.extend(parse_feed(source, url))
    print(f"[info] 収集 {len(pool)} 件", file=sys.stderr)
    return pool


def call_model_cli(prompt):
    """Claude Code CLI（サブスク枠・追加費用ゼロ）で生成する。メルマガ図解バッチと同方式。"""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError("claude CLIが見つかりません（npm install -g @anthropic-ai/claude-code）")
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)  # 誤ってAPI従量課金にならないようサブスク認証を強制
    proc = subprocess.run(
        [claude_bin, "-p", "--model", CLI_MODEL, "--output-format", "text"],
        input=prompt, capture_output=True, text=True, encoding="utf-8",
        timeout=600, env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLIが異常終了 (code {proc.returncode}): {proc.stderr[:300]}")
    return proc.stdout.strip()


def call_model_api(prompt):
    """Anthropic API（従量課金）で生成する。"""
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def call_model(prompt):
    """バックエンドを自動選択して生成。CLI優先（無料）、失敗時はAPIへフォールバック。"""
    backend = os.environ.get("NEWS_BACKEND") or (
        "cli" if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") else "api"
    )
    if backend == "cli":
        print(f"[info] バックエンド: Claude Code CLI（サブスク枠・model={CLI_MODEL}）", file=sys.stderr)
        try:
            return call_model_cli(prompt)
        except Exception as e:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise
            print(f"[warn] CLI失敗のためAPIへフォールバック: {e}", file=sys.stderr)
    print(f"[info] バックエンド: Anthropic API（従量課金・model={MODEL}）", file=sys.stderr)
    return call_model_api(prompt)


def curate(pool, today, recent=None):
    """Claudeに候補を渡して 5本＋展望のJSONを作らせる。"""
    catalog = "\n".join(
        f"- [{p['source']}] {p['title']} ({p['publishedAt'] or '日付不明'})\n  url: {p['url']}\n  snippet: {p['snippet']}"
        for p in pool
    )
    recent_block = "\n".join(f"- ({d}) {t}" for d, t in (recent or [])) or "（なし）"
    prompt = f"""あなたはAIニュースのキュレーターです。本日は {today} です。
以下は主要テックメディア（TechCrunch / The Verge / VentureBeat / MIT Technology Review / Bloomberg / Ars Technica / The Register / Wired / ZDNET / Reuters）のRSSから集めた候補記事です。
この中から、本日付近で重要なAIニュースを**必ず5本ちょうど**選び、日本語でまとめてください。

# 【最重要】重複禁止
次のリストは直近の配信で既に取り上げ済みのニュースです。同じ出来事・同じ発表は、URLや見出しの表現が違っても絶対に再掲しないでください。既出の続報を扱う場合は、新しい進展がある場合に限り、その新展開に絞って書くこと（前回と同じ内容の焼き直しは不可）。
--- 既出リスト（直近の配信済み見出し）---
{recent_block}
--- 既出リストここまで ---

# 選定方針
- 実行日（{today}）付近の最新記事を優先。古い記事・まとめ記事・薄い記事は避ける。
- 上記「既出リスト」と実質的に同じニュースは選ばない。新しいニュースだけで構成する。
- 業界横断（新モデル/企業・資金/規制・政策/研究 等）でインパクト順。媒体が偏らないよう努める。
- **必ず5本そろえること。** 突出した大ニュースが5本に満たない日は、候補の中から未報道（既出リストに無い）の注目度の高いAI記事で残り枠を埋め、5本にする。多少地味でも、数日内の新しい話題であればよい。
- ただし5本を埋めるために既出ニュースを再掲するのは厳禁。重複よりは内容の幅（別テーマ・別媒体）でカバーする。
- 候補の総数がどうしても5本に満たない極端な場合のみ、拾える本数だけにしてよい（その旨をnoteに書く）。

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
    text = call_model(prompt)
    # ```json フェンスが付いた場合に備えて剥がす
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 前後に文章が付いた場合に備え、最初の{〜最後の}を切り出して再試行
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e == -1:
            raise
        data = json.loads(text[s:e + 1])
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
    # 過去に配信済みの記事をプールから機械的に除外（同じニュースの複数日掲載を防ぐ）
    seen_urls, recent = published_history()
    fresh = [p for p in pool if norm_url(p["url"]) not in seen_urls]
    print(f"[info] 重複除外: {len(pool)}→{len(fresh)}件（既出 {len(seen_urls)}URL）", file=sys.stderr)
    # 全て既出になった場合のみ、元プールに戻す（モデル側の再掲禁止指示で最終的に重複を避ける）
    pool = fresh if fresh else pool
    data = curate(pool, today, recent)
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
