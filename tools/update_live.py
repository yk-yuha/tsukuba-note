"""「いま、つくばで」と「つくば山情報」の新着(live.js)を、公式サイトのRSSから作り直す。

載せるのは、見出し・日付・リンクだけ(本文は載せない)。
つくば市の分は、暮らしや おでかけに関わるものに絞る(職員募集や入札などは外す)。
使い方: python tools/update_live.py 出力先のlive.js
"""
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

JST = timezone(timedelta(hours=9))
FEEDS = [
    ("つくば市", "https://www.city.tsukuba.lg.jp/cgi-bin/feed.php"),
    ("観光コンベンション協会", "https://ttca.jp/feed/"),
    ("筑波観光鉄道", "https://www.mt-tsukuba.com/feed/"),
]
SOURCES = ["つくば市公式ウェブサイト", "つくば観光コンベンション協会", "筑波観光鉄道(ケーブルカー・ロープウェイ)"]
# つくば市の新着のうち、載せるもの・外すもの
CITY_KEEP = re.compile(r"イベント|まつり|祭|フェス|マルシェ|子育て|こども|子ども|親子|赤ちゃん|講座|教室|体験|休館|休園|開館|開園|公園|図書館|コンサート|演奏会|展|スポーツ|プール|花火|イルミネーション|通行止め|交通規制")
CITY_DROP = re.compile(r"職員|採用|入札|公募|プロポーザル|公告|契約|議会|審議会|委員|パブリックコメント|意見募集|指定管理|補助金|助成|予算|決算|人事|選挙|事業者")
MAX_AGE_DAYS = 45
MAX_ITEMS = 30


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "tsukuba-note.com feed reader"})
    return urllib.request.urlopen(req, timeout=30).read()


def text(el, name):
    for child in el:
        if child.tag.split("}")[-1] == name:
            return (child.text or "").strip()
    return ""


def link_of(el):
    for child in el:
        if child.tag.split("}")[-1] == "link":
            return (child.text or "").strip() or child.attrib.get("href", "")
    return ""


def when(el):
    for name in ("pubDate", "date", "updated", "published"):
        v = text(el, name)
        if not v:
            continue
        try:
            d = parsedate_to_datetime(v) if name == "pubDate" else datetime.fromisoformat(v.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=JST)
        return d.astimezone(JST)
    return None


EVENT_WORDS = re.compile(r"開催|ツアー|ハイク|まつり|祭|イベント|コンサート|演奏会|マルシェ|フェス")
EVENT_DATE_LABEL = re.compile(r"(?:日程|開催日|日時|期日|とき)\s*[:：]?\s*(?:令和\d+年|20\d\d年)?\s*(\d{1,2})月(\d{1,2})日")


def event_date(title, link, published):
    """イベントの日を見つける(過ぎたイベントをサイトに出さないため)。
    題名に日付があれば、サイトの側で読む。ないときだけ、ページの「日程」「開催日」などの行を読む。
    見つからなければ空(サイトの側で、発表から3週間で外す)。"""
    if re.search(r"\d{1,2}月\d{1,2}日|\d{1,2}/\d{1,2}", title.translate(str.maketrans("０１２３４５６７８９", "0123456789"))):
        return ""
    try:
        page = fetch(link).decode("utf-8", "ignore")
    except Exception:
        return ""
    page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    page = re.sub(r"<[^>]+>", " ", page).translate(str.maketrans("０１２３４５６７８９：", "0123456789:"))
    m = EVENT_DATE_LABEL.search(page)
    if not m:
        return ""
    d = datetime(published.year, int(m.group(1)), int(m.group(2)), tzinfo=JST)
    if d < published - timedelta(days=60):  # 年をまたぐ催し(12月の発表で1月の日付など)
        d = d.replace(year=d.year + 1)
    return d.strftime("%Y-%m-%d")


def main(out):
    now = datetime.now(JST)
    items, failed = [], []
    for src, url in FEEDS:
        try:
            root = ET.fromstring(fetch(url))
        except Exception as e:  # 1つ取れなくても、ほかは載せる
            failed.append(f"{src}: {e!r}"[:200])
            continue
        for el in root.iter():
            if el.tag.split("}")[-1] not in ("item", "entry"):
                continue
            title, link, d = text(el, "title"), link_of(el), when(el)
            if not title or not link.startswith("https://") or d is None:
                continue
            if (now - d).days > MAX_AGE_DAYS:
                continue
            if re.search(r"採用|求人", title):
                continue
            if src == "つくば市" and (CITY_DROP.search(title) or not CITY_KEEP.search(title)):
                continue
            item = {"date": d.strftime("%Y-%m-%d"), "title": re.sub(r"\s+", " ", title), "url": link, "src": src}
            if EVENT_WORDS.search(title):
                ev = event_date(title, link, d)
                if ev:
                    item["eventDate"] = ev
            items.append(item)
    if failed:
        print("取れなかった新着:", failed)
    if not items:
        print("新着が1件も取れなかったので、live.js は書き換えません")
        return 1
    items.sort(key=lambda x: x["date"], reverse=True)
    data = {"sources": SOURCES, "fetched": now.strftime("%Y-%m-%d"), "items": items[:MAX_ITEMS]}
    head = "// つくば市公式ウェブサイト、つくば観光コンベンション協会、筑波観光鉄道の新着情報(RSS)から、見出し・日付・リンクだけを自動で取りこんだもの。\n"
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + "window.LIVE = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n")
    print(len(data["items"]), "件を書きました")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
