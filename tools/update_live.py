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


# 週末の天気(気象庁の予報。茨城県南部)
# 近い2日ほどは、3時間ごとの天気(午前・午後のマーク)と、6時間ごとの降水確率(午前・午後・夜)。
# その先は、週間天気予報(茨城県全体)の1日1つのマークと降水確率。
JMA_URL = "https://www.jma.go.jp/bosai/forecast/data/forecast/080000.json"
JMA_3H_URL = "https://www.jma.go.jp/bosai/jmatile/data/wdist/VPFD/080020.json"
MAIN = {"1": "晴れ", "2": "くもり", "3": "雨", "4": "雪"}
WORSE = ["晴れ", "くもり", "雨", "雪"]  # 同じ時間帯に2つあるときは、右のほうを出す
POP_LABEL = {6: "午前", 12: "午後", 18: "夜"}

AMEDAS_TSUKUBA = "https://www.jma.go.jp/bosai/amedas/data/point/40336/{}_{:02d}.json"  # アメダス つくば(館野)


def observed(d, now):
    """その日の、これまでの観測の最高・最低気温(アメダス つくば)。"""
    vals = []
    for h in range(0, 24, 3):
        if datetime(d.year, d.month, d.day, h, tzinfo=JST) > now:
            break
        try:
            obs = json.loads(fetch(AMEDAS_TSUKUBA.format(d.strftime("%Y%m%d"), h)))
        except Exception:
            continue
        for k, v in obs.items():
            t = v.get("temp")
            if k[:8] == d.strftime("%Y%m%d") and t and t[0] is not None and t[1] == 0:
                vals.append(t[0])
    if not vals:
        return {}
    return {"hi": round(max(vals)), "lo": round(min(vals))}


def previous_weather(path):
    try:
        t = open(path, encoding="utf-8").read()
        return {x["date"]: x for x in json.loads(t[t.index("{"):t.rindex(";")]).get("weather", {}).get("days", [])}
    except Exception:
        return {}


def weekend_weather(now, prev=None):
    data = json.loads(fetch(JMA_URL))
    short, week = data[0], data[1]
    temps = {}  # 日付 -> {"hi": 最高, "lo": 最低}(土浦。つくばに近い観測点)
    try:
        v = json.loads(fetch(JMA_3H_URL))
        h3 = v["areaTimeSeries"]
        slots = [(datetime.fromisoformat(t["dateTime"]), w) for t, w in zip(h3["timeDefines"], h3["weather"])]
        pt = v["pointTimeSeries"]
        for t, hi, lo in zip(pt["timeDefines"], pt["maxTemperature"], pt["minTemperature"]):
            d = datetime.fromisoformat(t["dateTime"]).date()
            if hi != "":
                temps.setdefault(d, {})["hi"] = int(hi)
            if lo != "":
                temps.setdefault(d, {})["lo"] = int(lo)
    except Exception as e:
        print("3時間ごとの天気が取れなかった:", repr(e)[:200])
        slots = []
    wanted = [(now - timedelta(days=1)).date()] if now.weekday() == 6 else []
    for k in range(7):
        d = (now + timedelta(days=k)).date()
        if d.weekday() in (5, 6):
            wanted.append(d)
        if len(wanted) == 2:
            break
    days = []
    prev = prev or {}
    for d in wanted:
        day = {"date": d.isoformat(), "dow": "土日"[d.weekday() - 5]}
        if d < now.date():  # 過ぎた日: 天気のマークは前回の予報を残し、気温は1日の観測
            old = prev.get(d.isoformat(), {})
            day.update({k: old[k] for k in ("am", "pm", "main") if k in old})
            ob = observed(d, now)
            if ob and ("am" in day or "main" in day):
                day.update(ob, obs=True, src=old.get("src", "南部"))
                days.append(day)
            continue
        am = [w for t, w in slots if t.date() == d and 6 <= t.hour < 12 and w in WORSE]
        pm = [w for t, w in slots if t.date() == d and 12 <= t.hour < 18 and w in WORSE]
        if am and pm:
            day["am"] = max(am, key=WORSE.index)
            day["pm"] = max(pm, key=WORSE.index)
            ts = short["timeSeries"][1]
            area = next(a for a in ts["areas"] if a["area"]["code"] == "080020")
            pops = []
            for t, v in zip(ts["timeDefines"], area["pops"]):
                t = datetime.fromisoformat(t)
                if t.date() == d and t.hour in POP_LABEL and v != "":
                    pops.append({"label": POP_LABEL[t.hour], "pop": int(v)})
            day["pops"] = pops
            day["src"] = "南部"
            day.update(temps.get(d, {}))
        else:
            ts = week["timeSeries"][0]
            area = ts["areas"][0]
            for i, t in enumerate(ts["timeDefines"]):
                if datetime.fromisoformat(t).date() == d and area["weatherCodes"][i][:1] in MAIN:
                    day["main"] = MAIN[area["weatherCodes"][i][:1]]
                    day["pops"] = [{"label": "1日", "pop": int(area["pops"][i])}] if area["pops"][i] != "" else []
                    day["src"] = "県"
                    wt = week["timeSeries"][1]["areas"][0]  # 週間予報の気温は水戸
                    if wt["tempsMax"][i] != "":
                        day["hi"] = int(wt["tempsMax"][i])
                    if wt["tempsMin"][i] != "":
                        day["lo"] = int(wt["tempsMin"][i])
        if d == now.date():  # 今日: 予報に最低気温がないときは、けさの観測で補う
            ob = observed(d, now)
            if "lo" not in day and "lo" in ob:
                day["lo"], day["loObs"] = ob["lo"], True
        if "am" in day or "main" in day:
            days.append(day)
    if not days:
        return None
    rep = datetime.fromisoformat(short["reportDatetime"]).astimezone(JST)
    return {"report": rep.strftime("%Y-%m-%dT%H:%M"), "reportLabel": f"{rep.month}月{rep.day}日{rep.hour}時", "days": days}

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
    try:
        w = weekend_weather(now, previous_weather(out))
        if w:
            data["weather"] = w
    except Exception as e:  # 天気が取れなくても、新着は載せる
        print("天気が取れなかった:", repr(e)[:200])
    head = "// つくば市公式ウェブサイト、つくば観光コンベンション協会、筑波観光鉄道の新着情報(RSS)から、見出し・日付・リンクだけを自動で取りこんだもの。週末の天気は気象庁の予報(茨城県南部)。\n"
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + "window.LIVE = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n")
    print(len(data["items"]), "件を書きました")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
