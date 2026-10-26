# board #836 safety-test-data-marked
import datetime as dt
import email.utils
import json
import re

UTC = dt.timezone.utc


def rfc(t: dt.datetime) -> str:
    return email.utils.format_datetime(t.astimezone(UTC))


def rss(items, title="Feed") -> bytes:
    out = [f'<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>{title}</title>']
    for t, link, desc, when, img in items:
        out.append(f"<item><title><![CDATA[{t}]]></title><link>{link}</link><description><![CDATA[{desc}]]></description>"
                   f"<pubDate>{rfc(when)}</pubDate>" + (f'<media:content url="{img}" medium="image"/>' if img else "") + "</item>")
    out.append("</channel></rss>")
    return "".join(out).encode()


def atom(items, title="Feed") -> bytes:
    out = [f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>{title}</title>']
    for t, link, desc, when, _ in items:
        out.append(f'<entry><title>{t}</title><link rel="alternate" href="{link}"/><updated>{when.astimezone(UTC).isoformat()}</updated>'
                   f'<content type="html">{desc}</content></entry>')
    out.append("</feed>")
    return "".join(out).encode()


def feeds(now: dt.datetime) -> dict:
    h = lambda n: now - dt.timedelta(hours=n)
    long = lambda s: s + " " + "Officials said more details would follow later in the week, and residents were asked to check the city website."
    return {
        "https://feeds.npr.org/1001/rss.xml": rss([
            ("Senate passes stopgap bill to keep the government open", "https://www.npr.org/2026/10/13/stopgap-bill",
             long("The Senate passed a short-term spending bill on Tuesday, sending it to the House with three days left before a shutdown."), h(3), "https://media.npr.org/a.jpg"),
            ("Pope Leo has lung cancer, the Vatican announces after surgery", "https://www.npr.org/2026/10/13/pope-leo",
             long("The Vatican said Pope Leo had surgery to remove a tumor in his lung and is recovering in a Rome hospital."), h(5), None),
            ("Old story from last week", "https://www.npr.org/2026/10/05/old", "This is old news from last week.", h(200), None)]),
        "https://feeds.npr.org/1006/rss.xml": rss([
            ("What ice cream has to do with this year's Nobel Prize in economics", "https://www.npr.org/2026/10/13/nobel-ice-cream",
             long("This year's Nobel Prize in economics went to research on how prices respond to demand, explained with an ice cream stand."), h(8), None)]),
        "https://www.pbs.org/newshour/feeds/rss/headlines": rss([
            ("Senate passes stopgap bill to keep the government open", "https://www.pbs.org/newshour/politics/stopgap?utm_source=rss",
             "The bill now goes to the House.", h(2), None),
            ("Wildfire smoke drifts into the Midwest as fall fires burn", "https://www.pbs.org/newshour/nation/smoke",
             long("Smoke from fires in Canada drifted south into Minnesota and Wisconsin, lowering air quality."), h(6), None)]),
        "https://news.riverton.example/local.rss": rss([
            ("Riverton council weighs new rules for the harbor district", "https://news.riverton.example/2026-10-13/harbor-district-rules",
             long("The Riverton City Council heard public comment on land-use rules for the harbor district area on Monday night."), h(4), "https://riverton.example/t.jpg")]),
        "https://www.riverton-daily.example/feed": rss([
            ("River Transit adds Sunday service on Route 1", "https://www.riverton-daily.example/blog/route-1",
             long("River Transit will run Route 1 buses on Sundays starting in November, the agency said."), h(9), None)]),
        "https://lakeland-ledger.example/rss": rss([
            ("Lakeland buses cut routes as drivers run short", "https://lakeland-ledger.example/news/bus-route-cuts",
             long("The city bus service canceled several trips on Route 4 because of driver shortages."), h(7), None)]),
        "https://www.metro-times.example/metro-news/feed/": rss([
            ("New bike lanes on Main Street get an opening date", "https://www.metro-times.example/metro-news/main-street-bike-lanes",
             long("The city set an opening date for the new protected bike lanes on Main Street."), h(10), None)]),
        "https://hackaday.com/blog/feed/": rss([
            ("USBSID-Pico has Two SIDs, Many Inputs", "https://hackaday.com/2026/10/13/usbsid-pico",
             long("A Raspberry Pi Pico board plays two SID sound chips over USB for retro music fans."), h(2), None)]),
        "https://www.rtl-sdr.com/feed/": rss([
            ("BirriaSDR: 2.4 / 5 GHz SDR Discovery on a USB3.0 Wi-Fi Dongle", "https://www.rtl-sdr.com/birriasdr",
             long("A researcher found that a cheap USB 3.0 Wi-Fi dongle can be used as a 2.4 and 5 GHz software defined radio."), h(3), None)]),
        "https://www.arrl.org/news/rss": rss([
            ("ARRL Headquarters Closing Early on Friday", "https://www.arrl.org/news/hq-closing", "ARRL HQ will close early Friday.", h(150), None)]),
        "https://meshtastic.org/blog/rss.xml": rss([]),
        "https://github.com/meshtastic/firmware/releases.atom": atom([
            ("Meshtastic Firmware 2.8.1 Alpha", "https://github.com/meshtastic/firmware/releases/tag/v2.8.1", "Bug fixes for LoRa radios and new GPS options.", h(12), None)]),
        "https://www.cnx-software.com/feed/": rss([
            ("VIEWE launches 4.3-inch ESP32-S31 display development board", "https://www.cnx-software.com/2026/10/13/viewe-esp32",
             long("VIEWE launched a 4.3-inch touch display board built around the ESP32-S31 with Wi-Fi 6 and Bluetooth."), h(5), None)]),
        "https://www.bleepingcomputer.com/feed/": rss([
            ("Microsoft October Patch Tuesday fixes 6 zero-days", "https://www.bleepingcomputer.com/news/microsoft/october-2026-patch-tuesday",
             long("Microsoft's October 2026 Patch Tuesday fixes security flaws including 6 actively exploited zero-days."), h(4), "https://www.bleepstatic.com/p.jpg")]),
        "https://feeds.feedburner.com/TheHackersNews": rss([
            ("Small AI models could form self-propagating IoT botnets, researchers warn", "https://thehackernews.com/2026/10/ai-iot-botnets.html",
             long("Researchers warned that small AI models running on routers could spread between IoT devices like a worm."), h(6), None)]),
        "https://krebsonsecurity.com/feed/": rss([]),
        "https://www.darkreading.com/rss.xml": rss([
            ("Zero-Days Lurk in Microsoft's Overgrown October Patch Tuesday", "https://www.darkreading.com/vulns/zero-days-october",
             "Microsoft October Patch Tuesday fixes 6 zero-days in a large update.", h(5), None)]),
        "https://feeds.arstechnica.com/arstechnica/index": rss([
            ("FTC says Martin Shkreli violated his pharma ban on a livestream", "https://arstechnica.com/tech-policy/2026/10/shkreli",
             long("The FTC said Martin Shkreli broke his lifetime pharmaceutical industry ban during a livestream."), h(3), None)]),
        "https://www.theverge.com/rss/index.xml": atom([
            ("Apple's HomeView ad is confusing to everyone except parents", "https://www.theverge.com/2026/10/13/apple-homeview-ad",
             "Apple's new HomeView ad has viewers puzzled.", h(2), None)]),
        "https://techcrunch.com/feed/": rss([]),
        "https://spectrum.ieee.org/feeds/feed.rss": rss([]),
    }


def reddit(now):
    t = (now - dt.timedelta(hours=5)).timestamp()
    post = lambda title, sub, pts, out="": {"title": title, "link": f"https://www.reddit.com/r/{sub}/comments/x/{title[:8]}", "text": "", "image": "",
                                            "source": f"r/{sub}", "ts": dt.datetime.fromtimestamp(t, UTC).isoformat(), "points": pts, "comments": 12, "out": out}
    return {"radio": [post("My solar Meshtastic node survived the windstorm", "meshtastic", 412), post("First QSO on 10 meters!", "amateurradio", 300)],
            "hacking": [post("Writeup: breaking a smart lock over BLE", "netsec", 640), post("Hackaday covered my badge", "hacking", 50, "https://hackaday.com/x")]}


def hn(now):
    t = int((now - dt.timedelta(hours=3)).timestamp())
    return [{"title": "Gemini 4 Argon", "link": "https://deepmind.google/models/gemini/", "discussion": "https://news.ycombinator.com/item?id=1",
             "text": "", "image": "", "source": "Hacker News", "ts": dt.datetime.fromtimestamp(t, UTC).isoformat(), "points": 1086, "comments": 798},
            {"title": "A Hackaday story on HN", "link": "https://hackaday.com/2026/10/13/hn-story", "discussion": "https://news.ycombinator.com/item?id=2",
             "text": "", "image": "", "source": "Hacker News", "ts": dt.datetime.fromtimestamp(t, UTC).isoformat(), "points": 500, "comments": 90},
            {"title": "Show HN: I built a tool to track what AI leaders say", "link": "https://example.org/track", "discussion": "https://news.ycombinator.com/item?id=3",
             "text": "", "image": "", "source": "Hacker News", "ts": dt.datetime.fromtimestamp(t, UTC).isoformat(), "points": 320, "comments": 140}]


def mails(now: dt.datetime) -> dict:
    ts = lambda days, hours=0: int((now - dt.timedelta(days=days, hours=hours)).timestamp())
    m = lambda i, frm, subj, snip, t: {"id": f"m{i}", "thread_id": f"t{i}", "from": frm, "subject": subj, "snippet": snip, "ts": t, "date": ""}
    rows = [
        m(1, "Amazon.com <shipment-tracking@amazon.com>", 'Out for delivery: "Anker USB C Charger 40W" and 1 more item', "Arriving today 9 AM - 1 PM. Track your package.", ts(0, 2)),
        m(2, "Amazon.com <shipment-tracking@amazon.com>", 'Shipped: "USB-C Charging Cable"', "Arriving Thursday. Your package was shipped with USPS.", ts(1)),
        m(3, "AliExpress <transaction@notice.aliexpress.com>", "Your order 8123 has been shipped", "Your order of Desk Lamp is on its way via Cainiao. Estimated delivery: Oct 24", ts(2)),
        m(4, "USPS <auto-reply@usps.com>", "USPS® Expected Delivery Update", "Your package will be delivered by Friday between 2:00pm and 6:00pm.", ts(0, 5)),
        m(5, "eBay <ebay@ebay.com>", "Delivered: Phone Case Black", "Your package was delivered to the front door.", ts(1)),
        m(6, "Amazon.com <store-news@amazon.com>", "Deals for you: 40% off chargers", "Shop deals on chargers delivered fast", ts(0, 3)),
        m(7, "UPS <mcinfo@ups.com>", "UPS Update: Delivery Attempted", "We attempted delivery of your package from Mouser Electronics.", ts(0, 6)),
        m(8, "Etsy <transaction@etsy.com>", "Rate your recent purchase", "How did we do? Your order was delivered.", ts(2)),
        m(9, "USPS Informed Delivery <USPSInformeddelivery@email.informeddelivery.usps.com>", "Your Daily Digest for Tue, 10/13", "You have 3 mailpieces and 1 package arriving soon.", ts(0, 4)),
        m(10, "Amazon.com <shipment-tracking@amazon.com>", 'Shipped: "USB-C Charging Cable"', "Your package was shipped.", ts(2)),
        m(11, "Shop <no-reply@shop.app>", "Order #1042 confirmed", "Thanks for your order from Northwind Books. We'll let you know when it ships.", ts(3)),
    ]
    return {r["id"]: r for r in rows}


def events(today: dt.date, tz):
    at = lambda d, hh, mm: dt.datetime.combine(today + dt.timedelta(days=d), dt.time(hh, mm), tzinfo=tz).isoformat()
    return fake([{"id": "e1", "title": "College class: Art History 101", "start": at(0, 8, 15), "end": at(0, 9, 40), "all_day": False, "location": "Riverton City College"},
            {"id": "e2", "title": "Dentist", "start": at(0, 14, 30), "end": at(0, 15, 30), "all_day": False, "location": ""},
            {"id": "e3", "title": "Work block: write the report", "start": at(0, 16, 0), "end": at(0, 18, 0), "all_day": False, "location": ""},
            {"id": "e4", "title": "College class: Art History 101", "start": at(2, 8, 15), "end": at(2, 9, 40), "all_day": False, "location": "Riverton City College"},
            {"id": "e5", "title": "Riverton Book Fair", "start": (today + dt.timedelta(days=4)).isoformat(), "end": (today + dt.timedelta(days=5)).isoformat(), "all_day": True, "location": ""}])


def weather(place, days=4):
    base = dt.date.today()
    return {"place": "Riverton, Lakeland", "now": {"temp_f": 49.0, "feels_like_f": 46.0, "sky": "light rain", "wind_mph": 8.0},
            "days": [{"date": (base + dt.timedelta(days=i)).isoformat(), "summary": s, "high_f": hi, "low_f": lo, "rain_chance_pct": r, "rain_in": 0.1}
                     for i, (s, hi, lo, r) in enumerate([("light rain", 58, 46, 80), ("partly cloudy", 61, 45, 20), ("rain showers", 55, 47, 70), ("clear", 63, 44, 5),
                                                         ("overcast", 57, 48, 30), ("drizzle", 54, 47, 60), ("partly cloudy", 60, 45, 15)][:days])],
            "source": "open-meteo.com"}


class FakeModel:

    def __init__(self, bad=False):
        self.calls, self.bad, self.used, self.down, self.model = [], bad, 0, False, "fake"

    def ask(self, system, user, max_tokens=120, schema=None):
        self.calls.append(user)
        self.used += 1
        if "choosing sentences" in system:
            keys = [k for k in re.findall(r"^\[(\d+)\.1\]", user, re.M)][:5]
            if self.bad:
                return json.dumps({"keep": [f"{k}.1" for k in keys] + ["99.9", "Mayor Okafor said 4,000 people loved it."],
                                   "text": "Mayor Dana Okafor said 4,000 people \"loved every minute\" of it.",
                                   "rewrite": {"1.1": "The DOJ says the device was hidden."}})
            return json.dumps({"keep": [f"{k}.1" for k in keys]})
        if "WHAT was ordered" in system:
            return "unknown"
        return None


WORDS = ("council harbor budget transit school park bridge housing climate garden port library market vote court water "
         "neighbors mayor plan permit traffic weather storm crew engineer study").split()


def article_html(title: str, n_paras=9, seed=0, ld=False) -> str:
    paras = []
    for i in range(n_paras):
        w = [WORDS[(seed + i * 3 + k) % len(WORDS)] for k in range(6)]
        paras.append(f"The {w[0]} report on {title.split(':')[0][:40]} said work on the {w[1]} and the {w[2]} continues this week, officials said on "
                     f"Tuesday. Residents near the {w[3]} asked for more time, and the {w[4]} office said a decision on the {w[5]} would come by "
                     f"Friday after {10 + i} days of public comment.")
    body = "".join(f"<p>{t}</p>" for t in paras)
    ld_block = ('<script type="application/ld+json">' + json.dumps({"@type": "NewsArticle", "articleBody": "\n\n".join(paras)}) + "</script>") if ld else ""
    return (f'<html><head><meta property="og:image" content="https://img.example/{seed}.jpg">{ld_block}</head><body><nav><p>Home News Sports Weather '
            f'Opinion Obituaries Subscribe Today Menu Search</p></nav><article><h1>{title}</h1><div class="story">{body}</div>'
            f'<aside><p>Sign up for our newsletter to get the top stories in your inbox every single morning.</p></aside></article>'
            f'<footer><p>Copyright 2026 The Station. All rights reserved. Terms of use and privacy policy apply here.</p></footer></body></html>')


class FakePages:

    def __init__(self, n_paras=9, blocked=()):
        self.n, self.blocked, self.got = n_paras, set(blocked), []

    def get(self, url):
        self.got.append(url)
        if any(b in url for b in self.blocked):
            return None
        return article_html(url.rsplit("/", 1)[-1].replace("-", " "), self.n, seed=len(self.got), ld=len(self.got) % 2 == 0)


def png(w=640, h=300) -> bytes:
    import struct
    import zlib
    rows = b"".join(b"\x00" + bytes(((x // 20 + y // 20) % 2) * 200 + 30 for x in range(w)) for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def png_size(b: bytes) -> tuple[int, int]:
    import struct
    return struct.unpack(">II", b[16:24])


def cartoon_pick(day, recent, client=None, sizes=((390, 423), (402, 393), (614, 208))):
    import paper_cartoon
    out, n = [], 3310
    while len(out) < 3:
        if n not in recent:
            w, h = sizes[len(out) % len(sizes)]
            out.append({"num": n, "title": f"Test strip {n} (not a real xkcd)", "_fake": True, "alt": "The alt text that comes with the strip.",
                        "link": f"https://xkcd.com/{n}/", "image": png(2 * w, 2 * h), "ext": "png", "size": [w, h],
                        "credit": "xkcd by Randall Munroe (xkcd.com), CC BY-NC 2.5"})
            if paper_cartoon.complete(paper_cartoon.plan_rows([s["size"] for s in out])):
                break
        n -= 1
    return [out[i] for i in paper_cartoon.plan_rows([s["size"] for s in out])["order"]]


def town_events(day: dt.date):
    ev = []
    plan = {0: [("Brass Night at the Old Mill", "", "", "Riverton Events", "A brass band plays."),
                ("Science Saturday: Paper Rockets", "4 PM", "North Branch, Riverton Library", "Riverton Library", "Build and launch a paper rocket.")],
            1: [("Story Time for Toddlers", "10:30 AM", "Lakeside Library, Riverton Public Library", "Riverton Public Library", "Songs and stories."),
                ("Jazz Wednesdays at The Lantern", "", "", "Riverton Events", "Live jazz.")],
            2: [("Riverton Holiday Festival", "10 AM", "Riverton Expo Hall", "Riverton Expo Hall", "Festive exhibits and shopping.")],
            4: [("Farmers Market in Old Town", "9 AM", "Old Town", "Riverton Events", "Fresh produce and crafts."),
                ("Maker Lab: 3D printing basics", "1 PM", "Main Library, Riverton Library", "Riverton Library", "Learn to print.")],
            5: [("Free Museum Night and Downtown Art Walk", "5 PM", "Downtown Riverton", "Riverton Events", "Free museum night.")],
            6: [("Community clean-up", "9 AM", "Riverside Park", "Riverton Events", "Volunteers welcome.")]}
    for i, rows in plan.items():
        d = (day + dt.timedelta(days=i)).isoformat()
        for n, (title, time_, place, src, line) in enumerate(rows):
            ev.append({"title": title, "day": d, "time": time_, "place": place, "line": line, "link": f"https://example.org/event/{i}-{n}",
                       "source": src, "category": "", "sort": f"{d}T{n:02d}"})
    days = [(day + dt.timedelta(days=i)).isoformat() for i in range(7)]
    return {"items": fake(ev), "days": days, "counts": {d: sum(1 for e in ev if e["day"] == d) for d in days}, "notes": []}


def gmail_install(setattr_, now, accounts=None):
    import gmail
    mark(setattr_, "gmail")
    box = {k: dict(v, _fake=True) for k, v in mails(now).items()}
    setattr_(gmail, "accounts", lambda: accounts if accounts is not None else [{"name": "main", "email": "owner@gmail.com"}])
    setattr_(gmail, "list_ids", lambda name, q, n=40: list(box))
    setattr_(gmail, "get_meta", lambda name, mid: dict(box[mid]))
    return box


FAKE = ("calendar", "tasks", "gmail", "proposals", "weather", "feeds", "articles", "reddit", "hn", "events", "cartoon", "printer")


def mark(setattr_, *names):
    import paper
    setattr_(paper, "FAKE_SOURCES", frozenset(paper.FAKE_SOURCES) | set(names or FAKE))


def fake(items):
    return [dict(i, _fake=True) for i in items]


def install(setattr_, now):
    import gcal
    import paper
    mark(setattr_)
    fd = feeds(now)
    gmail_install(setattr_, now)
    setattr_(gcal, "list_events", lambda start, end, calendar_id="primary", query=None: [e for e in events(start.date(), gcal.TZ)])
    setattr_(paper, "printer_ready", lambda run=None, printer=None: (True, ""))
    setattr_(paper, "printers", lambda run=None: ["Office_Printer", "Office_Printer_2"])
    return {"fetch": lambda f: {"items": fake(paper.parse_feed(fd[f["url"]], f["name"]))} if f["url"] in fd else {"items": [], "error": f"{f['name']}: no answer"},
            "reddit": lambda subs: {k: fake(v) for k, v in reddit(now).items()}, "hn": lambda: fake(hn(now)), "weather_fn": weather, "photos": False,
            "pages": FakePages(), "events_fn": town_events, "cartoon_pick": cartoon_pick}


def as_json(x) -> str:
    return json.dumps(x, indent=1, default=str)


def busy_items(f: dict, now: dt.datetime, per_feed=12) -> list[dict]:
    words = "harbor council budget transit school park bridge housing climate garden port library market vote court water".split()
    base = f["url"].split("/feed")[0].rstrip("/")
    return [{"title": f"{f['name']} story {i}: {words[i % len(words)]} plan for the {words[(i * 7) % len(words)]}", "link": f"{base}/busy-{i}",
             "text": " ".join(f"The {words[(i + k) % len(words)]} report said work continues this week." for k in range(6)),
             "ts": (now - dt.timedelta(hours=1 + i)).isoformat(), "image": "", "source": f["name"]} for i in range(per_feed)]


def install_busy(setattr_, now, per_feed=12):
    kw = install(setattr_, now)
    import paper
    fd = feeds(now)
    kw["fetch"] = lambda f: {"items": fake((paper.parse_feed(fd[f["url"]], f["name"]) if f["url"] in fd else []) + busy_items(f, now, per_feed))}
    return kw


def install_quiet(setattr_, now):
    kw = install(setattr_, now)
    import paper
    fd = feeds(now)
    keep = {"https://feeds.npr.org/1001/rss.xml": 2, "https://news.riverton.example/local.rss": 1, "https://www.riverton-daily.example/feed": 1,
            "https://www.cnx-software.com/feed/": 1, "https://www.rtl-sdr.com/feed/": 1, "https://www.bleepingcomputer.com/feed/": 1}

    def fetch(f):
        if f["url"] not in keep:
            return {"items": []}
        return {"items": fake(paper.parse_feed(fd[f["url"]], f["name"])[:keep[f["url"]]])}
    kw.update(fetch=fetch, reddit=lambda subs: {}, hn=lambda: [], pages=FakePages(n_paras=20))
    return kw
