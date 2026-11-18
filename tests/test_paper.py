import base64
import datetime as dt
import email
import hashlib
import hmac
import time
import html
import json
import re
import shutil
from pathlib import Path

import httpx
import sys

import pytest
from fastapi.testclient import TestClient

import config
import gcal
import gmail
import inbox
import paper
import paper_fake
import paper_fun
import paper_render
import tasks

NOW = dt.datetime.now(gcal.TZ).replace(hour=7, minute=0, second=0, microsecond=0)
FIXED_DAY = dt.datetime(2026, 10, 22, 7, 0, tzinfo=gcal.TZ)


@pytest.fixture(autouse=True)
def _same_day_as_now(monkeypatch):
    # board #873 fix-paper-own-clock
    import paper
    monkeypatch.setattr(paper, "_now", lambda: NOW)
REAL_LIST_EVENTS, REAL_PRINTER_READY = gcal.list_events, paper.printer_ready


@pytest.fixture
def fakes(monkeypatch):
    return paper_fake.install(monkeypatch.setattr, NOW)


@pytest.fixture
def allow_real(monkeypatch):
    monkeypatch.setattr(paper, "real_guard", lambda *a, **k: None)
    monkeypatch.setattr(paper, "fake_loaded", lambda: False)


def _build(fakes, **kw):
    return paper.build(NOW.date(), now=NOW, model=kw.pop("model", paper_fake.FakeModel()), make_pdf=kw.pop("make_pdf", False), **{**fakes, **kw})


def test_rss_and_atom_feeds_are_read_with_their_real_titles_links_dates_and_images():
    fd = paper_fake.feeds(NOW)
    rss = paper.parse_feed(fd["https://feeds.npr.org/1001/rss.xml"], "NPR")
    assert rss[0]["title"] == "Senate passes stopgap bill to keep the government open"
    assert rss[0]["link"] == "https://www.npr.org/2026/10/13/stopgap-bill" and rss[0]["image"] == "https://media.npr.org/a.jpg"
    assert paper.when(rss[0]["ts"]) and "<" not in rss[0]["text"] and rss[0]["source"] == "NPR"
    atom = paper.parse_feed(fd["https://github.com/meshtastic/firmware/releases.atom"], "Meshtastic firmware")
    assert atom[0]["title"] == "Meshtastic Firmware 2.8.1 Alpha" and atom[0]["link"].endswith("/v2.8.1")
    html_desc = paper.parse_feed(paper_fake.rss([("A <b>bold</b> title", "https://x.org/1", "<p>Text with <a href='https://x.org'>a link</a> and "
                                                  "<img src='https://img.example/p.jpg'></p>", NOW, None)]), "X")[0]
    assert html_desc["title"] == "A bold title" and html_desc["text"] == "Text with a link and" and html_desc["image"] == "https://img.example/p.jpg"


def test_old_and_undated_items_are_left_out():
    items = [{"title": "new", "ts": (NOW - dt.timedelta(hours=3)).isoformat()}, {"title": "old", "ts": (NOW - dt.timedelta(hours=40)).isoformat()},
             {"title": "no date", "ts": None}, {"title": "future", "ts": (NOW + dt.timedelta(days=2)).isoformat()}]
    assert [i["title"] for i in paper.fresh(items, NOW)] == ["new"]


def test_the_same_story_from_two_feeds_is_kept_once():
    a = {"title": "Senate passes stopgap bill to keep the government open", "link": "https://www.npr.org/a"}
    b = {"title": "Senate passes stopgap bill to keep government open", "link": "https://www.pbs.org/b"}
    c = {"title": "Different story entirely about buses", "link": "https://www.npr.org/a?utm_source=rss"}
    assert paper.same_story(a, b) and paper.same_story(a, c)
    assert paper.dedupe([a, b, c, {"title": "Buses cancel routes again today", "link": "https://x.org/f"}]) == [a, {"title": "Buses cancel routes again today", "link": "https://x.org/f"}]


def test_news_sections_mix_sources_cap_hacking_and_never_repeat_hackaday(fakes):
    s = paper.settings()
    news = paper.news_sections(s, NOW, fetch=fakes["fetch"], reddit=fakes["reddit"], hn=fakes["hn"])
    nat = [i["title"] for i in news["national"]["items"]]
    assert nat.count("Senate passes stopgap bill to keep the government open") == 1 and "Old story from last week" not in nat
    hack = news["hacking"]["items"]
    assert len(hack) <= 8 and hack[0]["title"] == "Gemini 4 Argon"
    assert not any("hackaday" in (i["link"] + i.get("out", "")).lower() for i in hack)
    assert any(i["source"] == "r/netsec" for i in hack) and not any(i["title"] == "Hackaday covered my badge" for i in hack)
    assert any(i["source"] == "Hackaday" for i in news["radio"]["items"])
    every = [i["title"] for b in news.values() for i in b["items"]]
    assert len(every) == len(set(every))
    assert sum("Patch Tuesday" in t for t in every) == 1
    assert all(i["pos"] == n for b in news.values() for n, i in enumerate(b["items"]))


def test_a_dead_feed_costs_only_its_own_items(fakes):
    s = paper.settings()
    fd = paper_fake.feeds(NOW)
    fetch = lambda f: {"items": [], "error": f"{f['name']}: HTTP 500"} if f["name"] == "NPR" else {"items": paper.parse_feed(fd[f["url"]], f["name"])}
    news = paper.news_sections(s, NOW, fetch=fetch, reddit=lambda subs: {"error": "Reddit isn't connected (add a free Reddit app's keys to secrets/reddit.json)."}, hn=lambda: [])
    assert news["national"]["items"] and "NPR: HTTP 500" in news["national"]["notes"]
    assert "Reddit isn't connected (add a free Reddit app's keys to secrets/reddit.json)." in news["radio"]["notes"]


def test_hacker_news_front_page_comes_from_the_official_api_best_by_points():
    stories = {1: ("Low", 10), 2: ("High", 900), 3: ("Mid", 300)}

    def handle(req):
        if req.url.path.endswith("topstories.json"):
            return httpx.Response(200, json=[1, 2, 3, 4])
        i = int(req.url.path.rsplit("/", 1)[1].split(".")[0])
        if i == 4:
            return httpx.Response(200, json={"type": "job", "title": "Hiring"})
        t, pts = stories[i]
        return httpx.Response(200, json={"type": "story", "title": t, "url": f"https://e.org/{i}", "score": pts, "time": int(NOW.timestamp()), "descendants": 5})
    out = paper.hn_front(httpx.Client(transport=httpx.MockTransport(handle)), n=2)
    assert [i["title"] for i in out] == ["High", "Mid"] and out[0]["discussion"] == "https://news.ycombinator.com/item?id=2"


def test_reddit_top_of_the_day_one_request_per_section(monkeypatch):
    paper.REDDIT_KEYS.parent.mkdir(parents=True, exist_ok=True)
    paper.REDDIT_KEYS.write_text(json.dumps({"client_id": "rid", "client_secret": "rsec", "user": "example_user"}))
    calls = []
    t = NOW.timestamp() - 3600

    def handle(req):
        calls.append(req)
        if req.url.path == "/api/v1/access_token":
            return httpx.Response(200, json={"access_token": "tok"})
        kids = [{"data": {"id": "a", "title": "Node on the roof", "permalink": "/r/meshtastic/comments/a/x/", "subreddit": "meshtastic", "score": 50,
                          "num_comments": 3, "created_utc": t, "is_self": True, "selftext": "Pics inside"}},
                {"data": {"id": "b", "title": "NSFW", "permalink": "/r/x/b", "subreddit": "x", "score": 999, "created_utc": t, "over_18": True}},
                {"data": {"id": "c", "title": "Rules", "permalink": "/r/x/c", "subreddit": "x", "score": 999, "created_utc": t, "stickied": True}}]
        return httpx.Response(200, json={"data": {"children": kids}})
    import social
    monkeypatch.setattr(social, "REDDIT_PAUSE", 0)
    out = paper.reddit_top({"radio": ["meshtastic", "RTLSDR"], "hacking": ["netsec"]}, httpx.Client(transport=httpx.MockTransport(handle)))
    assert [i["title"] for i in out["radio"]] == ["Node on the roof"] and out["radio"][0]["link"] == "https://www.reddit.com/r/meshtastic/comments/a/x/"
    assert len(calls) == 3 and calls[1].url.path == "/r/meshtastic+RTLSDR/top" and calls[1].url.params["t"] == "day"
    assert "by /u/example_user" in calls[1].headers["user-agent"]


ARTICLE = ["The Riverton City Council voted 6-3 on Monday to approve new rules for the Riverside industrial area after months of debate.",
           'Council member Jane Doe said the vote "protects jobs and the bay" and called it a compromise between port tenants and neighbors.',
           "The rules take effect in January, and the port has until March to file comments on the next phase of the plan.",
           "Opponents said they would ask the state to review the decision before the end of the year."]


def test_the_article_page_gives_only_the_storys_own_paragraphs():
    import paper_articles
    m = paper_articles.main_text(paper_fake.article_html("Riverside vote", n_paras=6))
    assert len(m["paras"]) == 6 and all(p.startswith("The ") for p in m["paras"]) and m["image"] == "https://img.example/0.jpg"
    assert not any("newsletter" in p or "Copyright" in p or "Subscribe" in p for p in m["paras"])
    ld = paper_articles.main_text(paper_fake.article_html("Riverside vote", n_paras=6, ld=True))
    assert ld["paras"] == m["paras"]
    assert paper_articles.main_text("<html><p>Short.</p></html>")["paras"] == []


def test_sentences_are_exact_slices_of_the_article():
    import paper_articles
    t = 'The U.S. Department of Justice said Mr. Smith, 35, was arrested on Oct. 9. "It went well," he said. Then J. Doe left at 5 p.m. Friday.'
    got = [t[a:b] for a, b in paper_articles.sentence_spans(t)]
    assert got == ["The U.S. Department of Justice said Mr. Smith, 35, was arrested on Oct. 9.", '"It went well," he said.', "Then J. Doe left at 5 p.m. Friday."]


SOURCE = ARTICLE + ["According to the DOJ, OpenAI determined that the device had been concealed in toilet paper. It was found on September 30.",
                    "The company said it fired the employee. It referred the case to the FBI."]


def test_the_model_only_picks_sentences_and_the_story_is_the_articles_own_sentences():
    import paper_articles
    it = paper_articles.writeup({"title": "Riverside vote", "source": "Riverton Public Radio", "full": SOURCE}, paper_fake.FakeModel())
    assert it["writeup_by"] == "selected" and 3 <= len(it["writeup"]) <= 8
    src_sentences = {p[a:b] for p in SOURCE for a, b in paper_articles.sentence_spans(p)}
    assert all(p[a:b] in src_sentences for p in it["writeup"] for a, b in paper_articles.sentence_spans(p))
    assert it["writeup"][0] == ARTICLE[0] and it["writeup"][4] == SOURCE[4].split(". ")[0] + "."
    assert paper_articles.verbatim(it["writeup"], SOURCE) == (True, [])


def test_a_model_reply_that_rephrases_or_invents_is_ignored():
    import paper_articles
    it = paper_articles.writeup({"title": "Riverside vote", "source": "Riverton Public Radio", "full": SOURCE}, paper_fake.FakeModel(bad=True))
    text = " ".join(it["writeup"])
    assert it["writeup_by"] == "selected" and "Okafor" not in text and "4,000" not in text and "The DOJ says" not in text
    assert "According to the DOJ, OpenAI determined that the device had been concealed in toilet paper." in text
    src_sentences = {p[a:b] for p in SOURCE for a, b in paper_articles.sentence_spans(p)}
    assert all(p[a:b] in src_sentences for p in it["writeup"] for a, b in paper_articles.sentence_spans(p))
    for reply in ("Here is the story: The DOJ says the device was hidden.", '{"keep": "all"}', '{"keep": ["99.1", "x"]}', "", None):
        m = type("M", (), {"ask": lambda self, *a, r=reply, **k: r})()
        it = paper_articles.writeup({"title": "T", "source": "S", "full": SOURCE}, m)
        assert it["writeup_by"] == "lead" and it["writeup"] == paper_articles.lead_quote(SOURCE)


def test_the_verbatim_check_rejects_any_sentence_not_in_the_article():
    import paper_articles
    assert paper_articles.verbatim(ARTICLE[:3], ARTICLE) == (True, [])
    ok, problems = paper_articles.verbatim([ARTICLE[0], "The DOJ says the device had been concealed in toilet paper.", ARTICLE[2]], SOURCE)
    assert not ok and any("not in the article" in p for p in problems)
    assert not paper_articles.verbatim([ARTICLE[0]], ARTICLE)[0]


def test_a_model_that_is_down_is_asked_once_and_stories_are_the_lead_paragraphs():
    import paper_articles
    calls = []

    def post(*a, **k):
        calls.append(1)
        raise httpx.ConnectError("refused")
    m = paper.Model(post=post)
    for _ in range(2):
        it = paper_articles.writeup({"title": "T", "source": "S", "full": ARTICLE}, m)
        assert it["writeup_by"] == "lead" and it["writeup"] == ARTICLE
    assert len(calls) == 1 and m.down


def test_story_pages_are_read_politely_on_the_feeds_own_site_only():
    import paper_articles
    assert paper_articles.allowed_site("https://www.npr.org/2026/10/13/x", "https://feeds.npr.org/1001/rss.xml")
    assert paper_articles.allowed_site("https://thehackernews.com/2026/10/x.html", "https://feeds.feedburner.com/TheHackersNews", "thehackernews.com")
    assert not paper_articles.allowed_site("https://evil.example/x", "https://feeds.npr.org/1001/rss.xml")
    assert not paper_articles.allowed_site("http://www.npr.org/x", "https://feeds.npr.org/1001/rss.xml")
    seen = []

    def handle(req):
        seen.append(str(req.url))
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, text=paper_fake.article_html("x"), headers={"content-type": "text/html"})
    f = paper_articles.Fetcher(httpx.Client(transport=httpx.MockTransport(handle)), pause=0, sleep=lambda s: None)
    assert f.get("https://www.npr.org/a") and f.get("https://www.npr.org/a") is None
    assert f.get("https://www.npr.org/private/b") is None  # board #873 safety-robots-txt
    assert seen.count("https://www.npr.org/robots.txt") == 1 and "https://www.npr.org/private/b" not in seen
    assert all("Rosie morning paper" in paper_articles.UA for _ in [0])


def test_fewer_better_stories_and_briefs_for_the_rest(fakes):
    p = _build(fakes)["data"]
    nat = p["news"]["national"]["items"]
    mains = [i for i in nat if i.get("main")]
    assert 1 <= len(mains) <= paper.MAIN_CAPS["national"] and all(3 <= len(i["writeup"]) <= 8 for i in mains)
    assert all(i["writeup_by"] in ("selected", "lead") for i in mains)
    assert all(not i.get("main") and i.get("summary") is not None for i in nat if i not in mains)
    hn = [i for i in p["news"]["hacking"]["items"] if i["source"] == "Hacker News"]
    assert hn and not any(i.get("main") for i in hn)


def test_shipping_emails_are_read_by_rules_and_tricky_ones_are_skipped(monkeypatch):
    box = paper_fake.gmail_install(monkeypatch.setattr, NOW)
    today = NOW.date()
    read = {mid: paper.read_delivery(m, today) for mid, m in box.items()}
    assert read["m6"] is None and read["m8"] is None
    assert read["m1"]["status"] == "out for delivery" and read["m1"]["what"] == "Anker USB C Charger 40W" and read["m1"]["window"] == "9 AM–1 PM"
    assert read["m2"]["status"] == "shipped" and read["m2"]["carrier"] == "USPS"
    assert dt.date.fromisoformat(read["m2"]["expected"]).weekday() == 3
    assert read["m3"]["what"] == "Desk Lamp" and read["m3"]["carrier"] == "Cainiao" and read["m3"]["expected"].endswith("-10-24")
    assert read["m4"]["status"] == "shipped" and dt.date.fromisoformat(read["m4"]["expected"]).weekday() == 4 and read["m4"]["window"] == "2:00pm–6:00pm"
    assert read["m5"]["status"] == "delivered" and read["m5"]["expected"] is None
    assert read["m7"]["status"] == "problem" and read["m7"]["problem"] == "delivery attempted"
    assert read["m9"]["status"] == "mail today" and read["m9"]["store"] == "USPS"
    assert read["m11"]["status"] == "ordered" and read["m11"]["store"] == "Northwind Books"
    for r in read.values():
        if r:
            assert not re.search(r"\b(1Z|9[24]\d{18,})", json.dumps(r))
            assert r["expected"] is None or r["expected_words"]
    g = paper.deliveries(today)
    due = {m for m in ("m2", "m3", "m4") if read[m]["expected"] == today.isoformat()}
    assert {r["id"] for r in g["today"]} == {"m1", "m9"} | due and [r["id"] for r in g["delivered"]] == ["m5"]
    shown = [r["id"] for r in g["today"] + g["soon"]]
    assert shown.count("m2") == 1 and "m10" not in shown
    assert all(r["link"].startswith("https://mail.google.com/mail/u/owner@gmail.com/#inbox/t") for r in g["today"])


def test_deliveries_say_so_when_gmail_is_not_connected(monkeypatch):
    paper_fake.gmail_install(monkeypatch.setattr, NOW, accounts=[])
    assert paper.deliveries(NOW.date())["note"].startswith("Your Gmail isn't connected")


def test_delivery_lines_are_prose_from_the_emails_own_facts(monkeypatch):
    box = paper_fake.gmail_install(monkeypatch.setattr, NOW)
    r = paper.read_delivery(box["m1"], NOW.date())
    assert paper.delivery_line(r) == "Today, 9 AM–1 PM — Amazon: Anker USB C Charger 40W. Out for delivery."


def test_front_page_agenda_week_due_weather_and_headline(fakes):
    tasks.add("Finish lab report", NOW.date().isoformat())
    tasks.add("Mail the permit form", (NOW.date() + dt.timedelta(days=2)).isoformat())
    p = _build(fakes)
    fr = p["data"]["front"]
    tags = {a["what"]: a["tag"] for a in fr["agenda"]}
    assert tags["College class: Art History 101"] == "CLASS" and tags["Barber appointment"] == "APPOINTMENT" and tags["Work block: write the report"] == "WORK BLOCK"
    assert "Dentist" not in tags
    assert tags["Finish lab report"] == "DUE" and tags["Amazon: Anker USB C Charger 40W"] == "ARRIVING"
    assert fr["headline"].startswith("College class: Art History 101 at 8:15. Barber appointment at 2:30.")
    assert any(w["text"].startswith("College class: Art History 101 at 8:15") for w in fr["week"])
    assert {"when": "Today", "text": "Finish lab report"} in fr["due"] and any(x["text"] == "Mail the permit form" for x in fr["due"])
    assert fr["weather"]["days"][0]["high_f"] == 58


def test_an_empty_day_gets_an_honest_headline():
    h, dek = paper.day_headline([], [], [], {}, {"error": "x"})
    assert h == "A clear day: nothing on the calendar." and dek == ""
    h, _ = paper.day_headline([], [], [], {}, {"error": "x"}, "Google login expired (happens every 7 days). Run ./login.sh")
    assert h == "Your calendar couldn't be read this morning."


def test_a_calendar_that_cannot_be_read_is_never_shown_as_an_empty_day(fakes, monkeypatch):
    def broken(*a, **k):
        raise gcal.LoginExpired("Google login expired (happens every 7 days). Run ./login.sh")
    monkeypatch.setattr(gcal, "list_events", broken)
    d = _build(fakes)["data"]
    assert d["front"]["headline"].startswith("Your calendar couldn't be read this morning.")
    out = paper_render.render(d, "web") + paper_render.render_text(d)
    assert "Nothing on the calendar today" not in out and "A clear day" not in out and "Google login expired" in out


def test_the_calendar_is_only_ever_read(monkeypatch, fakes):
    seen = []

    class Ev:
        def __getattr__(self, name):
            seen.append(name)

            def call(**kw):
                return type("X", (), {"execute": lambda self: {"items": []}})()
            return call
    monkeypatch.setattr(gcal, "service", lambda: type("S", (), {"events": lambda self: Ev()})())
    monkeypatch.setattr(gcal, "list_events", REAL_LIST_EVENTS)
    _build(fakes)
    assert seen and set(seen) == {"list"}


def test_weather_can_ask_for_four_days(monkeypatch):
    import requests
    import web
    asked = []

    def get(url, params=None, timeout=None):
        asked.append(params)
        if "geocoding" in url:
            return type("R", (), {"json": lambda self: {"results": [{"name": "Riverton", "admin1": "Lakeland", "latitude": 40.0, "longitude": -100.0}]}})()
        d = {"time": ["2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"], "weather_code": [61, 2, 80, 0], "temperature_2m_max": [58, 61, 55, 63],
             "temperature_2m_min": [46, 45, 47, 44], "precipitation_probability_max": [80, 20, 70, 5], "precipitation_sum": [0.1, 0, 0.2, 0]}
        return type("R", (), {"json": lambda self: {"current": {"temperature_2m": 49, "apparent_temperature": 46, "weather_code": 61, "wind_speed_10m": 8}, "daily": d}})()
    monkeypatch.setattr(requests, "get", get)
    w = web.weather("Riverton", days=4)
    assert asked[1]["forecast_days"] == 4 and len(w["days"]) == 4


def test_the_week_ahead_lists_all_seven_days_and_says_when_a_day_has_nothing(fakes):
    d = _build(fakes)["data"]
    pr = paper_render.render(d, "print")
    a2 = pr.split('data-for="A2-main"')[1]
    today = NOW.date()
    heads = re.findall(r'<p class="u u-li lbl dayhead">([^<]+)</p>', a2)
    assert heads[:2] == ["Today", "Tomorrow"] and len(heads) == 7
    assert heads[2] == f"{today + dt.timedelta(days=2):%A, %B} {(today + dt.timedelta(days=2)).day}"
    empty = a2.split(f'dayhead">{heads[3]}</p>')[1].split("dayhead")[0]
    assert "Nothing listed yet." in empty
    assert a2.index("The week ahead around you") < a2.index('class="b story')
    a1 = pr.split('id="A1"')[1].split('id="A2"')[0]
    assert "The full week: A2" in a1 and "Farmers Market in Old Town" not in a1 and "Brass Night" in a1


def test_no_real_events_means_no_events_section(fakes):
    fakes["events_fn"] = lambda day: {"items": [], "notes": ["Riverton Events: HTTPStatusError"]}
    d = _build(fakes)["data"]
    assert "Things happening around you" not in paper_render.render(d, "print") + paper_render.render(d, "web")
    fakes["events_fn"] = paper_fake.town_events
    d = _build(fakes)["data"]
    web = paper_render.render(d, "web")
    assert "Things happening around you" in web and "Brass Night at the Old Mill" in web and "North Branch, Riverton Library" in web


def _xkcd(comics, hires=True):
    def handle(req):
        if req.url.host == "imgs.xkcd.com":
            name = req.url.path.rsplit("/", 1)[1].split(".")[0]
            n = int(name.replace("_2x", ""))
            if name.endswith("_2x"):
                if not hires:
                    return httpx.Response(404)
                w, h = paper_fake.png_size(comics[n][1])
                return httpx.Response(200, content=paper_fake.png(2 * w, 2 * h))
            return httpx.Response(200, content=comics[n][1])
        n = max(comics) if req.url.path == "/info.0.json" else int(req.url.path.strip("/").split("/")[0])
        if n not in comics:
            return httpx.Response(404)
        return httpx.Response(200, json={"num": n, "safe_title": comics[n][0], "alt": "alt " + str(n), "img": f"https://imgs.xkcd.com/comics/{n}.png", "link": ""})
    return httpx.Client(transport=httpx.MockTransport(handle))


REAL_1014 = {3310: ("Rejected Space Mission Profiles", (390, 423)), 3309: ("Dogcatcher", (402, 393)), 3308: ("Juice", (614, 208))}


def test_the_cartoons_are_real_xkcd_newest_first_never_repeated_within_60_days_and_readable():
    import paper_cartoon
    comics = {n: (t, paper_fake.png(*wh)) for n, (t, wh) in REAL_1014.items()}
    comics.update({3307: ("Fourth", paper_fake.png(600, 300)), 3306: ("Fifth", paper_fake.png(380, 400)), 3305: ("Sixth", paper_fake.png(410, 380))})
    client = _xkcd(comics)
    day1 = NOW.date().isoformat()
    c1 = paper.cartoon(day1, client)
    nums1 = [x["num"] for x in c1["strips"]]
    assert nums1 == [3310, 3309]
    assert all(x["credit"] == paper_cartoon.CREDIT and x["link"] == f"https://xkcd.com/{x['num']}/" and x["alt"] for x in c1["strips"])
    assert [x["size"] for x in c1["strips"]] == [[390, 423], [402, 393]]
    assert paper_fake.png_size((paper.DIR / day1 / c1["strips"][0]["file"]).read_bytes()) == (780, 846)
    c2 = paper.cartoon((NOW + dt.timedelta(days=1)).date().isoformat(), client)
    assert not set(nums1) & {x["num"] for x in c2["strips"]}
    assert paper.cartoon(day1, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))) is None
    one = paper_cartoon.pick_row(day1, set(), _xkcd({3308: ("Juice", paper_fake.png(614, 208))}, hires=False))
    assert [x["num"] for x in one] == [3308] and one[0]["scale"] == 1


@pytest.mark.parametrize("sizes", [[(390, 423), (402, 393), (614, 208)], [(614, 208)], [(600, 300), (800, 500), (700, 180)],
                                   [(300, 500), (300, 500), (640, 480), (700, 400)], [(500, 700), (900, 300), (800, 600)],
                                   [(740, 620), (500, 600), (900, 300)], [(1100, 180), (740, 200)]])
def test_the_cartoons_fill_the_text_width_and_print_readably(sizes):
    import paper_cartoon
    plan = paper_cartoon.plan_rows(sizes)
    assert plan["rows"] and plan["height"] <= paper_cartoon.MAX_TOTAL and len(plan["rows"]) <= 2
    for r in plan["rows"]:
        assert len(r["idx"]) <= 2 and abs(sum(r["widths"]) + paper_cartoon.GAP * (len(r["widths"]) - 1) - paper_cartoon.TEXT_W) < 1e-3
        assert len(r["idx"]) == 1 or all(sizes[i][0] / sizes[i][1] <= paper_cartoon.NARROW for i in r["idx"])
        for i, w in zip(r["idx"], r["widths"]):
            assert abs(w / (sizes[i][0] / sizes[i][1]) - r["height"]) < 1e-3
            assert paper_cartoon.letter_mm(sizes[i], w) >= 2.0, (sizes[i], w)
    assert paper_cartoon.letter_mm((614, 208), 7.2 / 3) < 2.0


def test_add_to_my_paper_takes_subreddits_topics_and_checked_feeds_only():
    paper.follow("subreddit", "r/sdr")
    paper.follow("topic", "lora antennas")
    paper.follow("feed", "https://hnrss.org/frontpage")
    s = paper.settings()
    assert "sdr" in s["subreddits"]["radio"] and "lora antennas" in s["watch"] and any(f["url"] == "https://hnrss.org/frontpage" for f in s["feeds"]["hacking"])
    for bad in (("feed", "https://evil.example/feed"), ("subreddit", "no spaces please"), ("other", "x")):
        with pytest.raises(ValueError):
            paper.follow(*bad)


def test_the_word_list_is_wordnet_with_cmu_pronunciation_and_big_enough_for_a_year():
    words = paper_fun.load_words()
    assert len(words) >= 1500 and len({w["word"] for w in words}) == len(words)
    assert all(re.fullmatch(r"[A-Z]{4,9}", w["word"]) and w["definition"] and w["pos"] in ("noun", "verb", "adjective", "adverb") for w in words)
    assert sum(1 for w in words if w["example"]) > 500 and {w["kind"] for w in words} == {"word", "biz", "tech", "radio"}
    assert all(w["say"] and w["phones"] and w["say"] == paper_fun.respell(w["phones"]) for w in words)
    assert all(w["word"][:max(4, len(w["word"]) - 3)].lower() not in w["definition"].lower() for w in words if w["kind"] == "word")
    head = "\n".join(paper_fun.WORDS.read_text(encoding="utf-8").splitlines()[:3])
    lic = (paper_fun.WORDS.parent / "paper_words.LICENSE").read_text(encoding="utf-8")
    cmu = (paper_fun.WORDS.parent / "paper_words.CMUDICT-LICENSE").read_text(encoding="utf-8")
    assert "WordNet 3.0" in head and "CMU Pronouncing" in head and "Princeton University" in lic and "Carnegie Mellon University" in cmu
    by = {w["word"]: w for w in words}
    assert by["ADAMANT"]["definition"] == "impervious to pleas, persuasion, requests, reason" and by["ADAMANT"]["say"] == "AD-uh-muhnt"


def test_respelling_from_cmudict_phones():
    assert paper_fun.respell("AE2 K W IY0 EH1 S") == "ak-wee-ESS"
    assert paper_fun.respell("AE1 D AH0 M AH0 N T") == "AD-uh-muhnt"
    assert paper_fun.respell("AE0 N T EH1 N AH0") == "an-TEN-uh"
    assert paper_fun.respell("AH0 B S K Y UH1 R") == "uhb-SKYOOR"
    assert paper_fun.respell("") == ""


def test_no_word_is_ever_used_twice_and_yesterdays_words_come_back_as_a_recap():
    seen = {}
    for n in range(10):
        day = (NOW + dt.timedelta(days=n)).date().isoformat()
        f = paper.fun(paper.settings(), day)
        got = [w["answer"] for w in f["words"]]
        assert len(got) == paper.WORDS_PER_DAY and not set(got) & set(seen), "a word came back"
        assert all(w["say"] and w["clue"] for w in f["words"])
        if n:
            prev = (NOW + dt.timedelta(days=n - 1)).date().isoformat()
            assert sorted(w["answer"] for w in f["yesterday"]) == sorted(w for w, d in seen.items() if d == prev)
        seen.update({w: day for w in got})
    day = (NOW + dt.timedelta(days=9)).date().isoformat()
    assert sorted(w["answer"] for w in paper.fun(paper.settings(), day)["words"]) == sorted(w for w, d in seen.items() if d == day)


PRIVATE_MAIL = [
    {"sender": "Example Lender <notices@lender.example>", "sender_name": "Collections", "subject": "Payment reminder",
     "summary": "Example Lender warns you missed a payment; pay $123.45 to bring the account current.", "snippet": ""},
    {"sender": "Example Card Bank <alerts@card.example>", "sender_name": "Example Card Bank", "subject": "Your account",
     "summary": "Example Card Bank expects a $40 past due balance by Friday.", "snippet": ""},
    {"sender": "Bob <bob@example.org>", "sender_name": "Bob", "subject": "Paperwork for the disability claim",
     "summary": "Can you send the form so I can file the disability claim?", "snippet": ""},
    {"sender": "Accounts <no-reply@example.com>", "sender_name": "Example", "subject": "Your verification code",
     "summary": "Your verification code is 381920.", "snippet": ""},
    {"sender": "Notices <info@agency.example>", "sender_name": "Agency", "subject": "Collections notice", "summary": "Final notice.", "snippet": ""},
    {"sender": "Pharmacy <rx@example.com>", "sender_name": "Neighborhood Rx", "subject": "Your prescription is ready", "summary": "", "snippet": ""},
]
HARMLESS_MAIL = [
    {"sender": "Amazon <shipment-tracking@amazon.com>", "sender_name": "Amazon", "subject": "Your Amazon order has shipped",
     "summary": "Your Amazon order has shipped and arrives Thursday.", "snippet": ""},
    {"sender": "Riverton Radio Club <news@rivertonradioclub.example>", "sender_name": "Riverton Radio Club", "subject": "October club newsletter",
     "summary": "This month: a fox hunt on Saturday and a talk on antennas.", "snippet": ""},
]


def test_personal_money_health_legal_and_security_mail_never_prints(fakes, monkeypatch):
    import mailwatch
    import paper_privacy
    for m in PRIVATE_MAIL:
        assert paper_privacy.why(m["sender_name"], m["subject"], m["summary"], sender=m["sender"]), m
    for m in HARMLESS_MAIL:
        assert not paper_privacy.why(m["sender_name"], m["subject"], m["summary"], sender=m["sender"]), (m, paper_privacy.why(m["sender_name"], m["subject"], m["summary"], sender=m["sender"]))
    for t in ("Example Lender missed payment $123.45", "Example Card Bank past due", "Paperwork for the disability claim", "Your verification code",
              "Collections notice", "Reset your password", "Your lab results are ready", "IRS notice about your taxes"):
        assert paper_privacy.private(t), t
    for t in ("Your Amazon order has shipped", "Riverton Radio Club newsletter", "Finish lab report for Art History 101", "Mail the library form"):
        assert not paper_privacy.private(t), t
    monkeypatch.setattr(mailwatch, "digest", lambda hours=24: {"total": 8, "needs_you": PRIVATE_MAIL + HARMLESS_MAIL, "suspicious": 0})
    monkeypatch.setattr(gmail, "accounts", lambda: [{"name": "main", "email": "owner@example.com"}])
    tasks.add("Pay the Example Card Bank bill", NOW.date().isoformat())
    d = _build(fakes)["data"]
    look = [x["who"] + " " + x["text"] for x in d["front"]["look"]]
    assert any("Amazon order has shipped" in x for x in look) and any("Radio Club" in x for x in look)
    out = paper_render.render(d, "print") + paper_render.render(d, "web") + paper_render.render(d, "email") + paper_render.render_text(d)
    for bad in ("Example Lender", "123.45", "Example Card", "past due", "disability", "verification code", "Collections", "prescription"):
        assert bad.lower() not in out.lower(), bad  # board #873 safety-private-mail-hidden
    seen = re.sub(r"<[^>]+>", " ", re.sub(r"(?s)<(style|script)[^>]*>.*?</(?:style|script)>", " ", out)).lower()
    assert not re.search(r"hidden|private|personal|filtered|withheld", seen)
    rows = {"today": [{"store": "CVS Pharmacy", "what": "Prescription refill", "status": "out for delivery"},
                      {"store": "Amazon", "what": "Anker USB C Charger 40W", "status": "out for delivery"}],
            "soon": [{"store": "Example Card Bank", "what": "Your new card", "status": "shipped"}], "delivered": []}
    kept = paper.private_out(rows)
    assert [r["store"] for r in kept["today"]] == ["Amazon"] and kept["soon"] == []


def test_needs_a_look_is_left_out_when_nothing_is_left(fakes, monkeypatch):
    import mailwatch
    monkeypatch.setattr(mailwatch, "digest", lambda hours=24: {"total": 2, "needs_you": PRIVATE_MAIL[:2], "suspicious": 0})
    monkeypatch.setattr(gmail, "accounts", lambda: [{"name": "main", "email": "owner@example.com"}])
    fr = paper.front(paper.settings(), NOW.date(), {"today": [], "soon": [], "delivered": []}, paper_fake.weather)
    assert fr["look"] == []
    assert "Needs a look" not in paper_render.dayblock({"front": fr, "day": NOW.date().isoformat(), "deliveries": None}, "print")


def _quote_tool():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_paper_quotes", Path(__file__).resolve().parent.parent / "tools" / "build_paper_quotes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_quote_list_is_real_sourced_cited_and_broad():
    qs = paper_fun.load_quotes()
    assert len(qs) >= 400 and len({q["person"] for q in qs}) >= 60
    assert len({q["id"] for q in qs}) == len(qs) and len({q["quote"].lower() for q in qs}) == len(qs)
    tool = _quote_tool()
    for q in qs:
        assert q["citation"] and re.search(r"\b(1\d{3}|20\d{2})\b", q["citation"]), q
        assert q["url"].startswith("https://en.wikiquote.org/wiki/") and q["role"]
        assert not tool.SKIP_HEAD.search(q["section"]) and not tool.SKIP_CITE.search(q["citation"]), q
        assert 30 <= len(q["quote"]) <= 200 and tool.good_quote(q["quote"]), q
    lic = (Path(paper_fun.__file__).parent / "paper_quotes.LICENSE").read_text(encoding="utf-8")
    assert "CC BY-SA" in lic and "wikiquote" in lic.lower()


def test_the_quote_builder_keeps_only_sourced_cited_quotes():
    tool = _quote_tool()
    wt = """== Quotes ==
* Nothing in life is to be feared, it is only to be understood.
** Speech to students, ''On Learning'' (1921), p. 4.
* A good quote with no citation is left out.
=== 1930s ===
* Imagination will often carry us to worlds that never were.
** Letter to a friend, 12 May 1934.
== Disputed ==
* Be less curious about people and more curious about ideas.
** Widely quoted (1990s), source unknown.
== Misattributed ==
* Science is a way of thinking much more than it is a body of knowledge.
** Actually Carl Sagan, Broca's Brain (1979).
== Attributed ==
* Life is like riding a bicycle, you must keep moving to keep your balance.
** Letter (1930).
== Quotes about Someone ==
* She was the finest mind of her time and we should learn from her.
** Obituary (1934).
"""
    got = [(q["quote"], tool.pick_cite(q)) for q in tool.parse(wt) if tool.pick_cite(q) and tool.good_quote(q["quote"])]
    assert got == [("Nothing in life is to be feared, it is only to be understood.", "Speech to students, On Learning (1921), p. 4."),
                   ("Imagination will often carry us to worlds that never were.", "Letter to a friend, 12 May 1934.")]


def test_a_quote_a_day_never_repeated_until_the_list_runs_out(fakes):
    qs = [{"id": f"q{i}", "quote": f"Quote {i} about learning.", "person": "P", "role": "r", "citation": "Book (1900)", "section": "Quotes", "url": "u"}
          for i in range(5)]
    used, seen = {}, []
    for k in range(5):
        day = f"2026-11-{k + 1:02d}"
        q = paper_fun.pick_quote(qs, used, day)
        assert paper_fun.pick_quote(qs, dict(used, **{q["id"]: day}), day) == q
        used[q["id"]] = day
        seen.append(q["id"])
    assert sorted(seen) == [q["id"] for q in qs]
    assert paper_fun.pick_quote(qs, used, "2026-11-06")["id"] == seen[0]
    d = _build(fakes)["data"]
    q = d["front"]["quote"]
    pr = paper_render.render(d, "print")
    a1 = pr.split('data-for="A1-')[0]
    assert html.escape(q["quote"]) in a1 and f'— {html.escape(q["person"])}, {html.escape(q["role"])}' in a1 and html.escape(q["citation"]) in a1
    assert '<h2 class="hed">' not in a1 and "Your day" in a1
    nxt = paper.quote((NOW + dt.timedelta(days=1)).date().isoformat())
    assert nxt["id"] != q["id"] and paper.quote(NOW.date().isoformat())["id"] == q["id"]


def test_the_weather_box_shows_the_whole_week_and_only_real_days(fakes):
    d = _build(fakes)["data"]
    w = d["front"]["weather"]
    assert len(w["days"]) == 7
    box = paper_render.weather_atom(d["front"])
    rows = re.findall(r"<tr><td>([^<]+)</td><td>([^<]+)</td><td class=\"r\">(\d+)/(\d+)</td><td class=\"r\">([^<]+)</td></tr>", box)
    assert len(rows) == 7 and rows[0][0] == "Today" and rows[0][1:] == ("light rain", "58", "46", "80%") and rows[6][4] == "15%"
    assert rows[1][0] == f"{NOW.date() + dt.timedelta(days=1):%a}" and "7-day forecast" in box
    short = dict(d["front"], weather=dict(w, days=w["days"][:4]))
    box4 = paper_render.weather_atom(short)
    assert box4.count("<tr><td>") == 4 and "4-day forecast" in box4


def test_sudoku_is_a_beginner_puzzle_with_one_solution_that_gets_harder_slowly():
    lo, hi = paper_fun.LEVELS["beginner"]
    for k in range(10):
        s = paper_fun.sudoku(f"2026-10-{12 + k}", "beginner", k)
        assert lo <= s["givens"] <= hi and s["level"] == "Beginner"
        assert paper_fun.valid_solution(s["solution"]) and paper_fun.count_solutions(s["puzzle"]) == 1
        assert paper_fun.singles_solve(s["puzzle"]) == s["solution"]
        assert all(v in (0, s["solution"][i]) for i, v in enumerate(s["puzzle"]))
    ramp = [paper_fun.want_givens("beginner", d) for d in range(0, 120)]
    assert set(ramp[:15]) == {48} and ramp == sorted(ramp, reverse=True) and ramp[30] in (42, 43) and min(ramp) == 36
    assert all(a - b <= 1 for a, b in zip(ramp, ramp[1:]))
    for level in ("easy", "medium"):
        lo, hi = paper_fun.LEVELS[level]
        for k in range(4):
            s = paper_fun.sudoku(f"2026-11-{10 + k}", level, 200)
            assert lo <= s["givens"] <= hi and paper_fun.count_solutions(s["puzzle"]) == 1, (level, s["givens"])
    e = paper_fun.sudoku("2026-11-01", "easy", 0)
    assert paper_fun.singles_solve(e["puzzle"]) == e["solution"]
    assert paper.save_settings({"sudoku": "medium"})["sudoku"] == "medium" and paper.settings()["sudoku"] == "medium"
    with pytest.raises(ValueError):
        paper.save_settings({"sudoku": "expert"})
    assert paper_fun.count_solutions([0] * 81) == 2
    broken = paper_fun.sudoku("x", "medium")["puzzle"][:]
    broken[broken.index(0)] = next(v for v in range(1, 10) if v not in paper_fun._cands(broken, broken.index(0)))
    assert paper_fun.count_solutions(broken) == 0


LINE = {"text": "Senate passes stopgap bill to keep the government open, isn't it?", "where": "the A1 story 'x'"}
WEEK = [f"2026-10-{d}" for d in range(12, 19)]


def test_a_different_cipher_each_weekday_and_every_one_decodes_to_the_real_line():
    import paper_cipher as pc
    kinds = [pc.make(d, LINE, "medium-hard", 0)["kind"] for d in WEEK]
    assert kinds == ["caesar", "swap", "atbash", "a1z26", "morse", "pigpen", "rail"]
    run = [pc.kind_for(f"2026-{m:02d}-{d:02d}") for m in (10, 11) for d in range(1, 29)]
    assert all(a != b for a, b in zip(run, run[1:]))
    for d in WEEK:
        for level in pc.LEVELS:
            c = pc.make(d, LINE, level, 0)
            assert pc.decode(c) == LINE["text"].upper() == c["plain"], (c["kind"], level)
            assert "Senate passes stopgap" not in str(c.get("words")) and "SENATE PASSES" not in str(c.get("words"))
            assert c["answer"].startswith(f"CIPHER ({c['name']}): {LINE['text'].upper()}") and "from the A1 story" in c["answer"]
    sw = pc.make("2026-10-13", LINE, "beginner", 0)
    key = sw["secret"]
    assert sorted(key) == sorted(key.values()) == list(pc.A) and all(k != v for k, v in key.items())
    flat = [t for w in sw["words"] for t in w]
    plain = [ch for ch in LINE["text"].upper() if ch != " "]
    assert all((key[p] if p.isalpha() else p) == t for p, t in zip(plain, flat))
    ca = pc.make("2026-10-12", LINE, "beginner", 0)
    assert all(pc.A[(pc.A.index(p) + ca["secret"]) % 26] == t for p, t in zip(plain, (t for w in ca["words"] for t in w)) if p.isalpha())
    assert pc.make("2026-10-14", LINE)["words"][0] == list("HVMZGV")
    assert pc.make("2026-10-15", LINE)["words"][0] == ["19", "5", "14", "1", "20", "5"]
    mo = pc.make("2026-10-16", LINE)["words"]
    assert mo[0] == ["...", ".", "-.", ".-", "-", "."] and mo[-3][-1] == "--..--" and ".----." in mo[-2]
    assert pc.rail_encode("RIVERTON", 2) == "RVROIETN"
    assert pc.rail_encode("WEAREDISCOVEREDFLEEATONCE", 3) == "WECRLTEERDSOEEFEAOCAIVDEN"


def test_cipher_hints_follow_the_level_and_are_right():
    import paper_cipher as pc
    assert pc.help_level("medium-hard", 0) == 0 and pc.help_level("medium-hard", 41) == 0 and pc.help_level("medium-hard", 42) == -1
    assert pc.help_level("beginner", 300) == -1 and pc.help_level("hard", 0) == -1
    for level, n in (("beginner", 3), ("easy", 2), ("medium", 2), ("medium-hard", 1), ("hard", 0)):
        c = pc.make("2026-10-13", LINE, level, 0)
        shown = re.findall(r"([A-Z]) = ([A-Z])", " ".join(c["hints"]))
        assert len(shown) == n and all(c["secret"][p] == x for x, p in shown), (level, shown)
        assert {v for w in c["filled"].values() for v in w.values()} == {p for _, p in shown}
        for wi, w in c["filled"].items():
            assert all(c["plain"].split(" ")[wi][ci] == v for ci, v in w.items())
    mh = pc.make("2026-10-12", LINE, "medium-hard", 0)
    assert mh["hints"] == ["The shift is between 1 and 25."] and not mh["filled"]
    b = pc.make("2026-10-12", LINE, "beginner", 0)
    assert b["hints"] == [f"The shift is {b['secret']}: move each letter back {b['secret']} places."] and b["filled"][0][0] == "S"
    for d, k in zip(WEEK[2:6], ("atbash", "a1z26", "morse", "pigpen")):
        c = pc.make(d, LINE, "medium-hard", 0)
        assert c["kind"] == k and not c["filled"] and not c["hints"]
        assert pc.make(d, LINE, "beginner", 0)["filled"][0] == dict(enumerate("SENATE"))
    r = pc.make("2026-10-18", LINE, "medium-hard", 0)
    assert r["rails"] == 3 and "example" not in r and not r["hints"]
    rb = pc.make("2026-10-18", LINE, "beginner", 0)
    assert rb["rails"] == 2 and rb["example"]["cipher"] == "SGAINL" and rb["hints"] == [f"The top rail holds the first {(pc.letters(LINE['text']) + 1) // 2} letters."]
    assert pc.pick_line([{"text": "Up 3% in 2026 as markets rally around the world today"}, {"text": "Too short to use"},
                         {"text": "Senate passes stopgap bill to keep the government open"}])["text"].startswith("Senate")


def test_the_cipher_prints_no_key_and_pigpen_draws_every_letter():
    import paper_cipher as pc
    data = {"fun": {"cipher": pc.make("2026-10-17", LINE, "medium-hard", 0)}}
    h = paper_render.cipher_block(data)
    letters = [ch for ch in LINE["text"].upper() if ch.isalpha()]
    assert h.count('<svg class="pp"') == len(letters) and "<img" not in h and "url(" not in h
    glyphs = {ch: pc.pigpen_svg(ch) for ch in pc.A}
    assert len(set(glyphs.values())) == 26
    assert all(glyphs[ch] in h for ch in set(letters))
    assert "Cipher · Pigpen" in h and "SENATE" not in h
    for d in WEEK:  # board #873 owner-cipher-no-key
        for level in pc.LEVELS:
            b = paper_render.cipher_block({"fun": {"cipher": pc.make(d, LINE, level, 0)}})
            assert "Key" not in b and "ckey" not in b and "ppkey" not in b and "Z Y X W" not in b and "A=1 B=2 C=3" not in b and "Q&nbsp;--.-" not in b
    mo = paper_render.cipher_block({"fun": {"cipher": pc.make("2026-10-16", LINE, "medium-hard", 0)}})
    assert "--..--" in mo and mo.count("<b>/</b>") == len(LINE["text"].split()) - 1
    ra = paper_render.cipher_block({"fun": {"cipher": pc.make("2026-10-18", LINE, "beginner", 0)}})
    assert "S . G . A ." in ra and "SGAINL" in ra


def test_the_cipher_hides_a_real_headline_from_that_days_paper(fakes):
    d = _build(fakes)["data"]
    c = d["fun"]["cipher"]
    lead = paper.front_three(d["news"])[0]
    assert c["text"] == lead["title"] and c["where"] == f"the A1 story '{lead['title']}'"
    pr = paper_render.render(d, "print")
    assert html.escape(c["text"]) in pr.split('data-for="A1-lead"')[1].split('data-for=')[0]
    assert pr.index("Cipher · ") < pr.index('class="b atom puz tail"')
    ans = paper_render.answers_html(d)
    flat = " ".join(t for _, t in paper_render.answer_lines(d))
    assert ans.index("CIPHER (") < ans.index("SUDOKU") and c["plain"] in flat and "from the A1 story" in flat
    assert paper.settings()["cipher"] == "medium-hard" and paper.save_settings({"cipher": "beginner"})["cipher"] == "beginner"
    with pytest.raises(ValueError):
        paper.save_settings({"cipher": "impossible"})


def test_masthead_dateline_footer_and_custom_title(fakes):
    paper.save_settings({"title": "The Riverton Times"})
    d = _build(fakes)["data"]
    pr = paper_render.render(d, "print")
    day = dt.date.fromisoformat(d["day"])
    assert '<h1 class="title mono">The Riverton Times</h1>' in pr and "rosie-blackletter.woff2" in pr and pr.count('class="pg"') == 4
    assert '<h1 class="title">The Riverton Times</h1>' in paper_render.render(dict(d, masthead="blackletter"), "print")
    assert "padding:0.35in 0.35in 0.62in 0.95in" in pr
    a = pr.index("— A4</footer>")
    assert pr.index('class="answers"') > a and re.search(r"\.answers\{[^}]*font-size:7\.2pt", pr)
    assert f"THE RIVERTON TIMES — {day:%B} {day.day}, {day.year}".upper() + " — A1" in pr and "— A4</footer>" in pr
    assert f"{day:%A, %B} {day.day}, {day.year}".upper() in pr and "@page{size:Letter;margin:0}" in pr and "<script>" in pr
    web = paper_render.render(d, "web", look="dark")
    assert "<script" not in web and "look-cyberdeck" in web and "@media (max-width:700px)" in web and "/vendor/fonts/rosie-blackletter.woff2" in web
    assert "Dear Rosie" not in pr + web and "crossword" not in (pr + web).lower() and "Things happening around you" in web
    em = paper_render.render(d, "email")
    assert "<script" not in em and "woff2" not in em and "/api/paper/img" not in em


def _rgb(c):
    c = c.lstrip("#")
    c = "".join(ch * 2 for ch in c) if len(c) == 3 else c
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _contrast(a, b):
    def lum(rgb):
        ch = [v / 255 for v in rgb]
        ch = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    hi, lo = sorted((lum(_rgb(a)), lum(_rgb(b))), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_theme_is_black_and_white_only_with_the_apps_type(fakes):
    d = _build(fakes)["data"]
    for mode in ("print", "web", "email"):
        out = paper_render.render(d, mode)
        style = out.split("<style>")[1].split("</style>")[0]
        for c in re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", style):
            r, g, b = _rgb(c)
            assert r == g == b, f"a colour in the {mode} CSS: {c}"
        for r, g, b in re.findall(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", style):
            assert r == g == b, "a colour in rgba()"
        assert "ui-monospace" in style and 'content:"> "' in style and "text-transform:uppercase" in style
    for theme in (paper_render.LIGHT, paper_render.DARK, paper_render.GRAY):
        v = dict(x.split(":") for x in theme.split(";"))
        assert _contrast(v["--ink"], v["--paper"]) >= 7 and _contrast(v["--mute"], v["--paper"]) >= 7, theme
    web = paper_render.render(d, "web")
    assert 'class="mode-web look-cyberdeck' in web and paper.settings()["look"] == "cyberdeck"
    dark = paper_render.render(dict(d, print_dark=True), "print")
    assert "printdark" in dark.split("<body")[1].split(">")[0] and "printdark" not in paper_render.render(d, "print").split("<body")[1].split(">")[0]
    assert paper.settings()["print_dark"] is False and paper.save_settings({"print_dark": True, "masthead": "blackletter"})["masthead"] == "blackletter"
    with pytest.raises(ValueError):
        paper.save_settings({"look": "neon"})


def test_page_one_leads_with_your_day_then_local_events_then_news(fakes):
    tasks.add("Finish lab report", NOW.date().isoformat())
    d = _build(fakes)["data"]
    for mode in ("print", "web"):
        a1 = paper_render.render(d, mode).split('id="A1"')[1].split('id="A2"')[0]
        day, wx, ev = a1.index("Your day"), a1.index("boxhead\">Weather"), a1.index("Things happening around you")
        news = a1.index('class="b story')
        assert day < a1.index("u-head\">Today") < ev < news and a1.index("u-head\">Arriving") < ev and a1.index("u-head\">Due") < ev
        assert day < wx < a1.index("u-head\">Today")
        assert 'class="evband"' in a1 and "Inside" in a1


def test_agenda_tags_are_right_aligned_and_feed_text_is_escaped(fakes, monkeypatch):
    fd = paper_fake.feeds(NOW)
    fd["https://feeds.npr.org/1001/rss.xml"] = paper_fake.rss([("<script>alert(1)</script> Big news", "https://npr.org/x", "Text " * 40, NOW, None)])
    fakes["fetch"] = lambda f: {"items": paper.parse_feed(fd[f["url"]], f["name"])} if f["url"] in fd else {"items": []}
    monkeypatch.setattr(gcal, "list_events", lambda *a, **k: paper_fake.events(NOW.date(), gcal.TZ) + [
        {"id": "x", "title": '<img src=x onerror="alert(1)">', "start": NOW.isoformat(), "end": NOW.isoformat(), "all_day": False, "location": ""}])
    html = paper_render.render(_build(fakes)["data"], "web")
    assert "<script>alert" not in html and "<img src=x" not in html and "&lt;img src=x onerror=" in html
    assert '<span class="g">CLASS</span>' in html and re.search(r"\.ag \.g\{[^}]*text-align:right", html)



def test_a_sensitive_calendar_event_is_left_out_of_the_paper_whole(fakes, monkeypatch):
    # board #873 owner-no-health-money-legal
    tomorrow = NOW + dt.timedelta(days=1)
    monkeypatch.setattr(gcal, "list_events", lambda *a, **k: paper_fake.events(NOW.date(), gcal.TZ) + [
        {"id": "p1", "title": "Disability review appointment", "start": NOW.replace(hour=12).isoformat(), "end": NOW.replace(hour=13).isoformat(),
         "all_day": False, "location": ""},
        {"id": "p2", "title": "Lunch", "start": NOW.replace(hour=12, minute=30).isoformat(), "end": NOW.replace(hour=13).isoformat(),
         "all_day": False, "location": "Pharmacy on 5th"},
        {"id": "p3", "title": "Therapy", "start": tomorrow.isoformat(), "end": tomorrow.isoformat(), "all_day": False, "location": ""}])
    d = _build(fakes)["data"]
    for mode in ("web", "print"):
        page = re.sub(r"url\([^)]*\)", "", paper_render.render(d, mode).lower())
        assert "disability" not in page and "therapy" not in page and "pharmacy" not in page and "private" not in page
    assert d["front"]["agenda"] and d["front"]["headline"]

def test_two_page_setting_makes_a1_plus_one_packed_page(fakes):
    paper.save_settings({"pages": 2})
    d = _build(fakes)["data"]
    pr = paper_render.render(d, "print", photo_dir=paper.DIR / d["day"])
    assert d["pages"] == 2 and pr.count('class="pg"') == 2 and "Words to learn" in pr and "Sudoku" in pr and "The cartoon" in pr
    with pytest.raises(ValueError):
        paper.save_settings({"pages": 3})


def _fake_pdf(pages_seq):
    seq = list(pages_seq)

    def make(html_path, pdf_path):
        n = seq.pop(0) if seq else 4
        pdf_path.write_bytes(b"%PDF-1.4\n" + b"".join(b"<< /Type /Page /Parent 1 0 R >>\n" for _ in range(n)) + b"<< /Type /Pages >>\n" + b"x" * 600)
    return make


def _fake_layout(fill):
    return lambda html_path: f'<html><body data-fill="{html.escape(json.dumps({"fill": fill, "raw": fill}))}">laid out</body></html>'


def test_the_print_copy_is_measured_and_never_kept_at_the_wrong_length(fakes):
    p = _build(fakes)
    full = {"A1": [1, 1, 1], "A2": [1, 1, 1], "A3": [1, 1, 1], "A4": [0.99, 1, 1]}
    st = paper.make_print_pdf(p["day"], pdf_fn=_fake_pdf([4]), layout_fn=_fake_layout(full))
    assert st["pages"] == 4 and st["pdf"] == "paper.pdf" and st["fill_ok"] and st["fill"]["fill"] == full
    st = paper.make_print_pdf(p["day"], pdf_fn=_fake_pdf([5]), layout_fn=_fake_layout(full))
    assert st["pdf"] == "" and "5 pages instead of 4" in st["pdf_note"] and not (paper.DIR / p["day"] / "paper.pdf").exists()
    st = paper.make_print_pdf(p["day"], pdf_fn=_fake_pdf([4]), layout_fn=_fake_layout({**full, "A3": [1, 0.5, 1]}))
    assert st["pdf"] == "paper.pdf" and not st["fill_ok"] and "50% full" in st["pdf_note"]
    st = paper.make_print_pdf(p["day"], pdf_fn=_fake_pdf([4]), layout_fn=lambda h: "<html><body>no engine</body></html>")
    assert st["pdf"] == "" and "layout didn't finish" in st["pdf_note"]


def foot_problems(pdf, page: int, answers: bool) -> list[str]:
    import subprocess
    dpi = 150
    raw = subprocess.run(["pdftoppm", "-gray", "-r", str(dpi), "-f", str(page), "-l", str(page), str(pdf)], capture_output=True).stdout
    m = re.match(rb"P5\s+(\d+)\s+(\d+)\s+(\d+)\s", raw)
    W, H = int(m.group(1)), int(m.group(2))
    px = raw[m.end():]
    dark = lambda x, y, t=128: px[y * W + x] < t
    L, R = round(0.95 * dpi), round((8.5 - 0.35) * dpi)
    rows = [y for y in range(H - 3 * dpi, H) if sum(1 for x in range(L, R, 2) if dark(x, y, 160)) >= 0.85 * (R - L) / 2]
    if not rows:
        return [f"page {page}: no footer rule found"]
    rule = rows[0]
    while rule - 1 in rows:
        rule -= 1
    out = []
    vert = {x for x in range(L - 3, R + 3) if all(dark(x, y, 170) for y in range(rule - 40, rule - 6))}
    near = {x + d for x in vert for d in (-1, 0, 1)}
    hit = [x for x in range(L - 3, R + 3) if x not in near and any(dark(x, y) for y in range(rule - 4, rule - 1))]
    if hit:
        out.append(f"page {page}: something touches the footer rule at x={hit[0] / dpi:.2f} in")
    low = [y for y in range(rule + 3, H) if any(dark(x, y) for x in range(0, W, 1))]
    if answers and not low:
        out.append(f"page {page}: no answers under the rule")
    for y in low:
        xs = [x for x in range(W) if dark(x, y)]
        if xs[0] < L - 2 or xs[-1] > R + 2:
            out.append(f"page {page}: answers past the text width ({xs[0] / dpi:.2f}..{xs[-1] / dpi:.2f} in)")
            break
    if low and low[-1] > (11 - 0.55) * dpi:
        out.append(f"page {page}: printed {(H - low[-1]) / dpi:.2f} in from the bottom edge")
    return out


PINNED_NOW = dt.datetime(2026, 10, 29, 7, 0, 0, tzinfo=gcal.TZ)


@pytest.mark.parametrize("kind,pages", [("busy", 4), ("quiet", 4), ("busy", 2), ("quiet", 2)])
def test_every_column_is_filled_on_a_busy_and_a_quiet_day_in_real_chrome(monkeypatch, kind, pages):
    if not paper.chrome_path() or not shutil.which("pdftoppm"):
        pytest.skip("Chrome or pdftoppm (poppler-utils) isn't installed")
    kw = (paper_fake.install_busy if kind == "busy" else paper_fake.install_quiet)(monkeypatch.setattr, PINNED_NOW)
    paper.save_settings({"pages": pages})
    p = paper.build(PINNED_NOW.date(), now=PINNED_NOW, model=paper_fake.FakeModel(), make_pdf=True, **kw)
    st = p["status"]
    assert st["pages"] == pages and st["pdf"] == "paper.pdf" and paper.pdf_pages(paper.DIR / p["day"] / "paper.pdf") == pages
    fill = st["fill"]
    assert sorted(fill["fill"]) == ["A1", "A2", "A3", "A4"][:pages] and all(len(c) == 3 for c in fill["fill"].values())
    assert min(v for c in fill["fill"].values() for v in c) >= 0.97, fill
    assert all(r >= 0.9 or g <= 4.0 for k in fill["raw"] for r, g in zip(fill["raw"][k], fill["gaps"][k])), fill
    assert st["fill_ok"]
    assert fill["toon"] and min(fill["toon"]) >= 0.97, fill["toon"]
    assert all(j >= 6 for j in fill["jumps"]), fill["jumps"]
    assert all(min(f) >= 3 for f in fill["frags"]), fill["frags"]
    assert fill["broken"] == [], fill["broken"]
    assert fill["tail"]["last_page"] and fill["tail"]["last_col"] and abs(fill["tail"]["gap"]) <= 2
    assert all(v >= 0.55 for v in fill["foot"].values()), fill["foot"]
    assert fill["edges"] == [], fill["edges"]
    pdf = paper.DIR / p["day"] / "paper.pdf"
    probs = [x for n in range(1, pages + 1) for x in foot_problems(pdf, n, answers=(n == pages))]
    assert probs == [], probs
    import subprocess
    text = subprocess.run(["pdftotext", str(paper.DIR / p["day"] / "paper.pdf"), "-"], capture_output=True, text=True).stdout.lower()
    assert "rest of this story" not in text and "read more" not in text and "continue reading" not in text
    if pages == 4:
        a2 = subprocess.run(["pdftotext", "-f", "2", "-l", "2", str(paper.DIR / p["day"] / "paper.pdf"), "-"], capture_output=True, text=True).stdout
        assert "THE WEEK AHEAD AROUND YOU" in a2.upper() and a2.upper().count("NOTHING LISTED YET") == 1
        a4 = subprocess.run(["pdftotext", "-f", "4", "-l", "4", str(paper.DIR / p["day"] / "paper.pdf"), "-"], capture_output=True, text=True).stdout
        assert fill["lost"] == [] and "CIPHER · " in a4.upper() and "CIPHER (" in a4
        assert fill["cipher"] == ["in"]
    else:
        assert fill["lost"] in ([], ["cipher"]) and (not fill["lost"]) == ("CIPHER (" in text.upper())
        assert fill["cipher"] == ([] if fill["lost"] else ["in"])


def test_a_long_answer_block_wraps_inside_the_printable_area_in_real_chrome(monkeypatch):
    if not paper.chrome_path() or not shutil.which("pdftoppm"):
        pytest.skip("Chrome or pdftoppm (poppler-utils) isn't installed")
    import paper_cipher
    monkeypatch.setattr(paper_cipher, "kind_for", lambda day: "caesar")
    kw = paper_fake.install(monkeypatch.setattr, NOW)
    p = paper.build(NOW.date(), now=NOW, model=paper_fake.FakeModel(), make_pdf=False, **kw)
    d = p["data"]
    t = "WATCH LIVE: Trump speaks at rally along Arizona-Nevada border as crowds gather in the heat"
    assert paper_cipher.letters(t) <= 90
    c = paper_cipher.make(d["day"], {"text": t, "where": f"the A1 story '{t}'"}, "medium-hard", 0)
    d["fun"]["cipher"], d["fun"]["key"]["cipher"] = c, c["answer"]
    paper._save(d["day"], d)
    lines = paper_render.answer_lines(d)
    assert all(len(x) <= paper_render.ANS_CHARS for _, x in lines) and " ".join(x for _, x in lines).count(t.upper()) == 1
    st = paper.make_print_pdf(d["day"])
    assert st["pages"] == 4 and st["fill"]["edges"] == [], st["fill"]["edges"]
    assert st["fill"]["cipher"] == ["in"] and st["fill"]["lost"] == []
    probs = [x for n in range(1, 5) for x in foot_problems(paper.DIR / d["day"] / "paper.pdf", n, answers=(n == 4))]
    assert probs == [], probs
    assert re.search(r"\.answers\{[^}]*font-size:7\.2pt", paper_render.render(d, "print"))


@pytest.mark.skipif(sys.platform == "win32", reason="print layout is measured with Linux fonts")
@pytest.mark.parametrize("kind", ["caesar", "swap", "atbash", "a1z26", "morse", "pigpen", "rail"])
def test_every_kind_of_cipher_prints_whole_in_one_column_in_real_chrome(monkeypatch, kind):
    if not paper.chrome_path():
        pytest.skip("Chrome isn't installed")
    import paper_cipher
    monkeypatch.setattr(paper_cipher, "kind_for", lambda day: kind)
    day = FIXED_DAY
    monkeypatch.setattr(paper, "_now", lambda: day)
    kw = paper_fake.install_quiet(monkeypatch.setattr, day)
    p = paper.build(day.date(), now=day, model=paper_fake.FakeModel(), make_pdf=True, **kw)
    fill = p["status"]["fill"]
    assert p["data"]["fun"]["cipher"]["kind"] == kind and p["status"]["pages"] == 4
    assert fill["cipher"] == ["in"] and fill["lost"] == [], fill
    if kind == "morse" and min(v for c in fill["fill"].values() for v in c) < 0.97:
        pytest.xfail("known: on a very quiet news day the tall Morse cipher can leave one back-page column short")
    assert min(v for c in fill["fill"].values() for v in c) >= 0.97 and fill["edges"] == [] and fill["broken"] == [], fill


class FakeCups:

    def __init__(self, printer="Office_Printer", pending=0, message="", disabled=False, missing=False, lp_rc=0):
        self.printer, self.pending, self.message, self.disabled, self.missing, self.lp_rc = printer, pending, message, disabled, missing, lp_rc
        self.log, self.job = [], f"{printer}-12"

    def __call__(self, cmd, **kw):
        self.log.append(cmd)
        R = lambda rc=0, out="", err="": type("R", (), {"returncode": rc, "stdout": out, "stderr": err})()
        if cmd[:2] == ["lpstat", "-p"]:
            if self.missing or cmd[2] != self.printer:
                return R(1, "", f"lpstat: Invalid destination name in list \"{cmd[2]}\".")
            head = f"printer {self.printer} " + ("disabled since Tue -" if self.disabled else "is idle.  enabled since Tue 13 Oct 2026")
            return R(0, head + (f"\n\t{self.message}" if self.message else "") + "\n")
        if cmd[:2] == ["lpstat", "-o"]:
            if self.pending > 0:
                self.pending -= 1
                return R(0, f"{self.job}   owner   181248   Tue 13 Oct 2026 09:40:01 PM UTC\n")
            return R(0, "")
        if cmd[0] == "lp":
            return R(self.lp_rc, "" if self.lp_rc else f"request id is {self.job} (1 file(s))\n", "lp: error - printer said no" if self.lp_rc else "")
        return R(1)

    def lp_calls(self):
        return [c for c in self.log if c[0] == "lp"]


def test_printing_uses_lp_one_sided_on_the_direct_queue_and_never_past_the_last_page(allow_real, fakes, monkeypatch):
    monkeypatch.setattr(paper, "printer_ready", REAL_PRINTER_READY)
    assert paper.settings()["printer"] == ""
    paper.save_settings({"printer": "Office_Printer"})
    p = _build(fakes)
    paper.make_print_pdf(p["day"], pdf_fn=_fake_pdf([4]), layout_fn=_fake_layout({"A1": [1, 1, 1]}))
    cups, watched = FakeCups(), []
    r = paper.print_paper(p["day"], run=cups, watch=lambda *a: watched.append(a))
    lp = cups.lp_calls()[-1]
    assert r["ok"] and r["job"] == "Office_Printer-12" and lp[:3] == ["lp", "-d", "Office_Printer"]
    assert "sides=one-sided" in lp and "page-ranges=1-4" in lp and lp[-1].endswith("paper.pdf") and lp[lp.index("-n") + 1] == "1"
    assert "print-scaling=none" in lp
    assert watched == [(p["day"], "Office_Printer-12", "Office_Printer")]
    for cups, why in ((FakeCups(missing=True), "isn't set up"), (FakeCups(disabled=True, message="Paused by the owner"), "paused")):
        r = paper.print_paper(p["day"], run=cups, watch=lambda *a: watched.append(a))
        assert not r["ok"] and why in r["note"] and cups.lp_calls() == []
    assert "The printer said no" in paper.print_paper(p["day"], run=FakeCups(lp_rc=1), watch=lambda *a: None)["note"]
    paper.save_settings({"printer": "Office_Printer_2"})
    cups = FakeCups(printer="Office_Printer_2")
    assert paper.print_paper(p["day"], run=cups, watch=lambda *a: None)["ok"] and cups.lp_calls()[0][2] == "Office_Printer_2"
    with pytest.raises(ValueError):
        paper.save_settings({"printer": "Printer; rm -rf /"})


def test_a_printed_job_is_watched_until_it_leaves_the_queue(fakes):
    p = _build(fakes)
    cups, slept = FakeCups(pending=2), []
    assert paper.watch_job(p["day"], cups.job, cups.printer, run=cups, sleep=slept.append) == "done"
    assert len(slept) == 3 and "job Office_Printer-12 finished" in paper.for_day(p["day"])["status"]["print_note"]
    assert cups.lp_calls() == [] and not [i for i in inbox.list_items() if "waiting at the printer" in i["title"]]


def test_a_stuck_job_gets_one_clear_inbox_note_and_is_never_sent_again(fakes):
    p = _build(fakes)
    cups = FakeCups(pending=99)
    assert paper.watch_job(p["day"], cups.job, cups.printer, run=cups, sleep=lambda s: None, now=NOW) == "stuck"
    assert cups.lp_calls() == [] and sum(1 for c in cups.log if c[:2] == ["lpstat", "-o"]) == paper.WATCH_CHECKS
    notes = [i for i in inbox.list_items() if i["title"] == "The paper is still waiting at the printer: is it on?"]
    assert len(notes) == 1 and "5 min" in notes[0]["body"] and "cancel Office_Printer-12" in notes[0]["body"] and notes[0]["link"] == "#/paper"
    paper.watch_job(p["day"], cups.job, cups.printer, run=cups, sleep=lambda s: None, now=NOW)
    assert len([i for i in inbox.list_items() if "waiting at the printer" in i["title"]]) == 1
    cups = FakeCups(pending=99, message="No suitable destination host found by cups-browsed.")
    cups.job = "Office_Printer-13"
    assert paper.watch_job(p["day"], cups.job, cups.printer, run=cups, sleep=lambda s: None, now=NOW) == "stuck"
    assert sum(1 for c in cups.log if c[:2] == ["lpstat", "-o"]) == 2
    body = next(i["body"] for i in inbox.list_items() if "Office_Printer-13" in i["body"])
    assert "No suitable destination host found by cups-browsed" in body and cups.lp_calls() == []


class FakeGmailService:
    def __init__(self):
        self.sent = []

    def __call__(self, name):
        svc = self

        class M:
            def send(self, userId, body):
                svc.sent.append(body["raw"])
                return type("X", (), {"execute": lambda self: {"id": "sent-1"}})()

        class U:
            def messages(self):
                return M()
        return type("S", (), {"users": lambda self: U()})()


def _own_account():
    cfg = config.load()
    cfg["gmail_accounts"] = [{"name": "main", "email": "owner@gmail.com"}]
    config.save(cfg)


def test_send_own_refuses_anyone_but_your_own_addresses():
    _own_account()
    svc = FakeGmailService()
    for to in (["someone@else.com"], ["owner@gmail.com", "someone@else.com"], [], ["owner@gmail.com\nBcc: x@y.z"]):
        with pytest.raises(gmail.NotYourAddress):
            gmail.send_own("main", to, "s", "<p>h</p>", "t", service_fn=svc)
    assert svc.sent == []
    paper.save_settings({"email_to": ["Me@Work.example"]})
    gmail.send_own("main", ["owner@gmail.com", "me@work.example"], "The Rosie Times\r\nBcc: x@y.z", "<p>h</p>", "t", [("p.pdf", b"%PDF")], service_fn=svc)
    msg = email.message_from_bytes(base64.urlsafe_b64decode(svc.sent[0]))
    assert msg["To"] == "owner@gmail.com, me@work.example" and msg["Cc"] is None and msg["Bcc"] is None and "\n" not in msg["Subject"]
    assert [p.get_filename() for p in msg.walk() if p.get_filename()] == ["p.pdf"]
    with pytest.raises(ValueError):
        paper.save_settings({"email_to": ["a@b.co", "c@d.co", "e@f.co", "g@h.co"]})


def test_the_morning_run_notes_prints_and_emails_once_a_day(allow_real, fakes, monkeypatch):
    monkeypatch.setattr(paper, "printer_ready", REAL_PRINTER_READY)
    _own_account()
    paper.save_settings({"printer": "Office_Printer"})
    p = _build(fakes)
    paper.make_print_pdf(p["day"], pdf_fn=_fake_pdf([4]), layout_fn=_fake_layout({"A1": [1, 1, 1]}))
    sent, cups, watched = [], FakeCups(), []
    send = lambda *a: sent.append(a)
    out = paper.deliver_paper(p["day"], NOW, run=cups, send=send, watch=lambda *a: watched.append(a))
    assert out["print"]["ok"] and out["email"]["ok"] and len(sent) == 1 and sent[0][1] == ["owner@gmail.com"] and sent[0][5][0][0].endswith(".pdf")
    paper.deliver_paper(p["day"], NOW, run=cups, send=send, watch=lambda *a: watched.append(a))
    assert len(sent) == 1 and len(cups.lp_calls()) == 1 and len(watched) == 1
    items = [i for i in inbox.list_items() if i["title"] == "Your morning paper is ready"]
    assert len(items) == 1 and items[0]["link"] == "#/paper" and "Print: Sent 4 pages" in items[0]["body"]


def test_a_failed_email_is_noted_once_and_not_retried(allow_real, fakes):
    _own_account()
    p = _build(fakes)
    calls = []

    def send(*a):
        calls.append(1)
        raise RuntimeError("Your Gmail login can't send mail (it's read-only).")
    paper.save_settings({"print_on": False})
    paper.deliver_paper(p["day"], NOW, send=send)
    paper.deliver_paper(p["day"], NOW, send=send)
    assert len(calls) == 1
    fails = [i for i in inbox.list_items() if i["title"] == "The morning paper couldn't be emailed"]
    assert len(fails) == 1 and "read-only" in fails[0]["body"]


def test_every_section_built_from_test_data_is_stamped_and_the_page_watermarked(fakes):
    d = _build(fakes)["data"]
    assert set(paper_fake.FAKE) <= set(d["fake"])
    for mode in ("print", "web", "email"):
        out = paper_render.render(d, mode, photo_dir=paper.DIR / d["day"])
        assert "TEST DATA — NOT REAL. This copy was made from stand-in data" in out
        heads = ("Your day", "Today", "Arriving", "Weather", "Things happening around you")
        for head in heads:
            assert re.search(re.escape(head) + r'\s*<span class="fake">TEST DATA — NOT REAL</span>', out), (mode, head)
        if mode != "email":
            assert out.count('<div class="wm" aria-hidden="true">TEST DATA — NOT REAL</div>') == 4
    assert any(i.get("_fake") for b in d["news"].values() for i in b["items"])
    assert all(r.get("_fake") for g in ("today", "soon", "delivered") for r in d["deliveries"][g])
    assert "TEST DATA — NOT REAL" in paper_render.render_text(d).splitlines()[2]
    real_news = dict(d, fake=["gmail", "calendar"])
    out = paper_render.render(json.loads(json.dumps(real_news).replace('"_fake": true', '"_real": true')), "print")
    assert re.search(r'Arriving\s*<span class="fake">', out) and not re.search(r'Weather\s*<span class="fake">', out)
    assert "fakenews" not in out.split("<body")[1].split(">")[0] and "fakedata" in out.split("<body")[1].split(">")[0]


def test_a_paper_from_real_sources_has_no_stamp(fakes, monkeypatch):
    d = _build(fakes)["data"]
    clean = json.loads(json.dumps(d).replace('"_fake": true', '"_real": true'))
    clean["fake"] = []
    for mode in ("print", "web", "email"):
        out = paper_render.render(clean, mode).split("<script>")[0]
        assert "TEST DATA" not in out and 'class="wm"' not in out and 'class="fake"' not in out and "fakeband" not in out.split("</style>")[1]


def test_real_deliveries_refuse_while_test_data_is_loaded(fakes, client):
    p = _build(fakes)
    assert paper.fake_loaded()
    with pytest.raises(paper.FakeDataError):
        paper.morning(NOW)
    with pytest.raises(paper.FakeDataError):
        paper.build_async()
    with pytest.raises(paper.FakeDataError):
        paper.deliver_paper(p["day"], NOW)
    assert "test data" in paper.print_paper(p["day"], run=lambda *a, **k: pytest.fail("lp was called"))["note"]
    assert "test data" in paper.email_paper(p["day"], send=lambda *a: pytest.fail("mail was sent"))["note"]
    r = client.post("/api/paper/make")
    assert r.status_code == 409 and "test data" in r.json()["error"]
    import automations
    started = []
    automations.job_paper(NOW, config.load()) if not started else None
    assert not paper.state["running"]


def test_a_paper_made_from_test_data_is_never_printed_even_later(fakes, monkeypatch):
    p = _build(fakes)
    monkeypatch.setattr(paper, "fake_loaded", lambda: False)
    assert "made from test data" in paper.print_paper(p["day"], run=lambda *a, **k: pytest.fail("lp was called"))["note"]
    assert "made from test data" in paper.email_paper(p["day"], send=lambda *a: pytest.fail("mail was sent"))["note"]


def test_a_shorter_version_is_the_articles_own_sentences_in_order_and_keeps_the_lead():
    import paper_articles
    text, index = paper_articles.numbered(SOURCE)
    keys = ["1.1", "5.2", "3.1", "2.1", "5.1", "4.1", "6.1", "6.2"]
    full, sents = paper_articles.by_rank(keys, SOURCE, index)
    flat = [(t, r) for ps in sents for t, r in ps]
    src_order = [SOURCE[pi][a:b] for k in sorted(index, key=lambda k: index[k]) for pi, a, b in [index[k]]]
    for budget in range(1, len(flat) + 1):
        kept = [t for t, r in flat if r < budget]
        assert kept[0] == ARTICLE[0].split(". ")[0] + "." or kept[0].startswith("The Riverton City Council")
        assert [src_order.index(t) for t in kept] == sorted(src_order.index(t) for t in kept)
        assert all(t in src_order for t in kept)
    lead, ls = paper_articles.lead_and_end(SOURCE * 3)
    assert lead[0] == SOURCE[0] and lead[-1] == SOURCE[-1]
    assert [r for t, r in ls[0]] == list(range(len(ls[0]))) and min(r for t, r in ls[-1]) == len(ls[0])


def test_no_story_ever_points_to_a_website_for_its_ending(fakes):
    d = _build(fakes)["data"]
    for mode in ("print", "web", "email"):
        out = paper_render.render(d, mode).lower()
        for phrase in ("rest of this story", "read more", "continue reading", "full story at", "read the rest"):
            assert phrase not in out.split("<script>")[0], (mode, phrase)
    assert "rest of this story" not in paper_render.LAYOUT_JS


def test_newsletters_and_roundups_are_never_full_stories(fakes):
    import paper_articles
    title = "Zelenskyy sits down with NPR. And, Pope Leo diagnosed with lung cancer"
    url = "https://www.npr.org/2026/10/14/g-s1-148100/up-first-newsletter-zelenskyy-npr-pope-leo-lung-cancer"
    assert paper_articles.is_roundup(title, url) and paper_articles.is_roundup(title, "https://www.npr.org/2026/10/14/x/y")
    assert paper_articles.is_roundup("Morning briefing: what to know today", "https://e.org/a") and paper_articles.is_roundup("x", "https://e.org/live-updates/x")
    assert paper_articles.is_roundup("x", "https://e.org/a", "<html>" + "<h2>One</h2><p>a</p>" * 6 + "</html>")
    assert not paper_articles.is_roundup("Pope Leo has lung cancer, the Vatican announces after surgery", "https://www.npr.org/2026/10/13/g-s1-147968/pope-leo-has-lung-cancer")
    fd = paper_fake.feeds(NOW)
    fd["https://feeds.npr.org/1001/rss.xml"] = paper_fake.rss([(title, url, "Good morning. Here's your news roundup for today. " * 4, NOW, None)])
    fakes["fetch"] = lambda f: {"items": paper_fake.fake(paper.parse_feed(fd[f["url"]], f["name"]))} if f["url"] in fd else {"items": []}
    d = _build(fakes)["data"]
    up = next(i for i in d["news"]["national"]["items"] if i["title"] == title)
    assert not up.get("main") and up["summary"].startswith("Good morning.")


@pytest.mark.parametrize("title,start,want,headline", [
    ("6:30 Wake up + get ready", "06:30", "Wake up + get ready", "Wake up + get ready at 6:30."),
    ("7:00 Breakfast", "07:00", "Breakfast", "Breakfast at 7."),
    ("7:30 Gym with Sam (2 hrs)", "07:30", "Gym with Sam (2 hrs)", "Gym with Sam (2 hrs) at 7:30."),
    ("06:30 AM Gym", "06:30", "Gym", "Gym at 6:30."),
    ("7:00 - Breakfast", "07:00", "Breakfast", "Breakfast at 7."),
    ("4:00 Wrap-up + shutdown", "16:00", "Wrap-up + shutdown", "Wrap-up + shutdown at 4."),
    ("Sams Dental Appointment at 14:00", "13:45", "Sams Dental Appointment at 14:00", "Sams Dental Appointment at 14:00."),
    ("5:00 Cook dinner from scratch", "17:30", "5:00 Cook dinner from scratch", "5:00 Cook dinner from scratch."),
    ("2 tickets for the show", "19:00", "2 tickets for the show", "2 tickets for the show at 7."),
])
def test_a_titles_own_start_time_is_not_printed_twice(title, start, want, headline):
    iso = f"{NOW.date().isoformat()}T{start}:00-07:00"
    assert paper.clean_title(title, iso) == want
    ev = [{"id": "x", "title": title, "start": iso, "end": iso, "all_day": False, "location": ""}]
    assert paper.day_headline(ev, [], [], {}, {"error": "x"})[0] == headline


def test_the_morning_job_runs_once_a_day_at_the_owners_time(allow_real, fakes, monkeypatch):
    import automations
    runs = []
    _build(fakes)
    monkeypatch.setattr(paper, "morning", lambda now=None, **kw: runs.append(now) or paper._status(now.date().isoformat(), morning_done="x"))

    class Sync:
        def __init__(self, target, args=(), kwargs=None, **k):
            self.go = lambda: target(*args, **(kwargs or {}))

        def start(self):
            self.go()
    monkeypatch.setattr(automations.threading, "Thread", Sync)
    cfg = config.load()
    early = NOW.replace(hour=5, minute=59)
    automations.job_paper(early, cfg)
    assert runs == []
    automations.job_paper(NOW.replace(hour=6, minute=0), cfg)
    automations.job_paper(NOW.replace(hour=6, minute=30), cfg)
    automations.job_paper(NOW.replace(hour=9, minute=0), cfg)
    assert len(runs) == 1
    paper.save_settings({"time": "05:00", "on": False})
    automations.job_paper((NOW + dt.timedelta(days=1)).replace(hour=6), cfg)
    assert len(runs) == 1


def test_a_failed_morning_run_is_tried_once_more_after_20_minutes():
    day = NOW.date().isoformat()
    assert paper.claim_run(day, NOW)
    assert not paper.claim_run(day, NOW + dt.timedelta(minutes=5))
    assert paper.claim_run(day, NOW + dt.timedelta(minutes=21))
    assert not paper.claim_run(day, NOW + dt.timedelta(hours=3))


def test_job_paper_is_in_the_automations_list():
    import automations
    assert automations.job_paper in automations.JOBS and automations.JOBS[-1] is automations.job_mail


@pytest.fixture
def client(isolated, monkeypatch):
    import server
    (isolated / "secrets").mkdir(exist_ok=True)
    (isolated / "secrets" / "session_key").write_text("testkey-0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(server, "SECRETS", isolated / "secrets")
    c = TestClient(server.app)
    c.cookies.set("assistant_session", hmac.new(b"testkey-0123456789abcdef0123456789abcdef", b"assistant-ok", hashlib.sha256).hexdigest())
    return c


def test_api_needs_login_and_serves_the_paper_without_scripts(client, fakes):
    import server
    anon = TestClient(server.app)
    assert anon.get("/api/paper").status_code == 401 and anon.get("/api/paper/view").status_code == 401
    d = client.get("/api/paper").json()
    assert d["paper"] is None and d["settings"]["pages"] == 4 and d["printer"]["name"] == ""
    p = _build(fakes)
    d = client.get("/api/paper").json()
    assert d["paper"]["headline"].startswith("College class") and d["days"] == [p["day"]]
    v = client.get(f"/api/paper/view?day={p['day']}&look=dark")
    assert v.status_code == 200 and "script-src" not in v.headers["content-security-policy"] and "default-src 'none'" in v.headers["content-security-policy"]
    assert "<script" not in v.text and "look-cyberdeck" in v.text
    assert client.get("/api/paper/img/2026-10-13/..%2F..%2Fsecrets").status_code == 404
    assert client.get("/api/paper/img/2026-10-13/img1.jpg").status_code == 404
    assert client.get("/api/paper?day=../../x").status_code == 400


def test_api_settings_and_follow_are_checked(client):
    assert client.post("/api/paper/settings", json={"time": "6am"}).status_code == 400
    assert client.post("/api/paper/settings", json={"email_to": ["not an email"]}).status_code == 400
    assert client.post("/api/paper/settings", json={"feeds": {"national": [{"name": "x", "url": "http://insecure.example/feed"}]}}).status_code == 400
    assert client.post("/api/paper/settings", json={"nonsense": 1}).status_code == 400
    r = client.post("/api/paper/settings", json={"time": "05:45", "sections": {"fun": False}, "copies": 2})
    assert r.status_code == 200 and r.json()["settings"]["time"] == "05:45" and r.json()["settings"]["sections"]["fun"] is False
    assert client.post("/api/paper/follow", json={"kind": "feed", "value": "https://evil.example/rss"}).status_code == 400
    assert client.post("/api/paper/follow", json={"kind": "topic", "value": "e-ink displays"}).json()["settings"]["watch"] == ["e-ink displays"]


def test_api_print_says_why_when_there_is_no_paper(client):
    r = client.post("/api/paper/print", json={})
    assert r.status_code == 409 and "no paper" in r.json()["error"]


def test_the_owners_own_hidden_senders_are_kept_out():
    import config
    import paper_privacy
    assert not paper_privacy.private("Hello", sender="News <hi@smalllender.example>", money=False)
    cfg = config.load()
    cfg["paper"] = {"hide_senders": ["smalllender.example", "@other.example", "not a domain"]}
    config.save(cfg)
    assert paper_privacy.hidden_senders() == ("smalllender.example", "other.example")
    assert paper_privacy.why("Hello", sender="News <hi@mail.smalllender.example>", money=False) == "domain smalllender.example"
    for sender in ('"alice@ok.example" <hi@smalllender.example>', "Help @Home <hi@mail.smalllender.example>",
                   "a@ok.example, b@smalllender.example", "hi@smalllender.example.", "x@@smalllender.example"):
        assert paper_privacy.why("Hello", sender=sender, money=False) == "domain smalllender.example", sender
    assert paper_privacy.why("Hello", sender='"a@ok.example" <noreply@dfas.mil>', money=False) == "domain dfas.mil"
    assert not paper_privacy.why("Hello", sender='"smalllender.example" <a@ok.example>', money=False)
    assert not paper_privacy.why("Hello", sender="a@notsmalllender.example", money=False)


def test_a_long_sender_name_does_not_push_the_address_out_of_the_stored_sender():
    import paper_privacy
    assert paper_privacy.short_sender("Bob <b@ok.example>") == "Bob <b@ok.example>"
    assert paper_privacy.short_sender(None) == ""
    for frm in ("N" * 400 + " <noreply@dfas.mil>", "a@ok.example " + "N" * 400 + " <x@dfas.mil>", "N" * 199 + "<x@dfas.mil>"):
        kept = paper_privacy.short_sender(frm)
        assert len(kept) <= 200, len(kept)
        assert paper_privacy.why("Hello", sender=kept, money=False) in ("domain dfas.mil", "sender too long to check"), kept[-40:]
    import pathlib
    app = pathlib.Path(paper_privacy.__file__).parent
    src = (app / "mailwatch.py").read_text() + (app / "paper.py").read_text()
    assert src.count("paper_privacy.short_sender(") == 2 and 'frm[:200]' not in src and 'msg["from"][:200]' not in src


def test_a_shortened_sender_is_hidden_whenever_the_full_sender_is():
    import random
    import paper_privacy
    rnd = random.Random(1118)
    doms = ["dfas.mil", "ok.example", "a1.com", "sub." * 40 + "dfas.mil", "x" * 120 + ".example", "mail.dfas.mil", "d.example",
            "\u03bcission-lane.com", "\u0394iscover.com", "dfas\ufffc.mil", "=?UTF-8?B?A?=.mil"]
    hidden = shown = 0
    for _ in range(4000):
        parts = [rnd.choice(["N" * rnd.randint(1, 180), "Bob", '"a@ok.example"', ""]) + " <u@" + rnd.choice(doms) + ">" for _ in range(rnd.randint(1, 30))]
        frm = ", ".join(parts)
        kept = paper_privacy.short_sender(frm)
        assert len(kept) <= 200, len(kept)
        if len(frm) <= 200:
            assert kept == frm
        if len(frm) > 200:
            assert kept.endswith(" @" + paper_privacy.UNCHECKED) and paper_privacy.why("Hello", sender=kept, money=False), frm[:300]
        if paper_privacy.why("Hello", sender=frm, money=False):
            hidden += 1
            assert paper_privacy.why("Hello", sender=kept, money=False), frm[:300]
        else:
            shown += 1
    assert hidden > 500 and shown > 50, (hidden, shown)
    for frm in ("x" * 250 + " <a@ok.example>, Bob <b@\u03bcission-lane.com>", "x" * 250 + " <b@\u0394iscover.com>", "N" * 201):
        assert paper_privacy.why("Hello", sender=paper_privacy.short_sender(frm), money=False), frm[-30:]
    assert len(paper_privacy.short_sender("N" * 300, 10)) <= len(" @" + paper_privacy.UNCHECKED)
    many = "x" * 150 + " <a@a1.com>, " + ", ".join(f"u@d{i}.example" for i in range(30)) + ", Bob <b@dfas.mil>"
    assert paper_privacy.why("Hello", sender=paper_privacy.short_sender(many), money=False)
    assert paper_privacy.why("Hello", sender=paper_privacy.short_sender("x" * 150 + " <a@" + "sub." * 40 + "dfas.mil>"), money=False)


def test_the_check_never_raises_and_hides_what_it_cannot_read(monkeypatch):
    import random
    import paper_privacy
    for bad in ("=?UTF-8?B?A?=", "Your order =?UTF-8?B?A?= has shipped", "=?UTF-8?B?QQ==?= =?x?B?A?="):
        assert paper_privacy.why(bad), bad
        assert paper_privacy.why("Hello", sender=bad + " <a@ok.example>", money=False), bad
    rnd = random.Random(1119)
    bits = ["=?", "?=", "?B?", "?Q?", "UTF-8", "x", "A", "=", "RGVidA", "\ud800", "\x00", "\ufffc", "\u03a9", "\u00df", " ", "@", "<", ">", "\n", "utf-16", "=FF", "_"]
    for _ in range(20000):
        text, sender = ("".join(rnd.choice(bits) for _ in range(rnd.randint(1, 12))) for _ in range(2))
        assert isinstance(paper_privacy.why(text, 7, None, b"x", ["y"], sender=sender, money=rnd.random() < 0.5), str)
    monkeypatch.setattr(paper_privacy, "_check", lambda *a: 1 / 0)
    assert paper_privacy.why("Hello") == "text that could not be checked"
    assert paper_privacy.private("Hello")


def test_encoded_word_text_never_unhides_what_the_plain_reading_hides(monkeypatch):
    import random
    import config
    import paper_privacy
    for texts, sender in ((("=?" + "z" * 40, "1234"), "code"), (("=?", "credit"), "card Services <a@b.com>"), (("Your PIN =? x", "credit"), "Union <a@b.com>"),
                          (("=?UTF-8?B?Y3JlZGl0?=", "card"), "Bob <a@b.com>"), (("your", "=?UTF-8?B?Y3JlZGl0?="), "card Services <a@b.com>")):
        assert paper_privacy.why(*texts, sender=sender), (texts, sender)
    rnd = random.Random(1120)
    bits = ["credit", "card", "union", "code", "1234", "debt", "hello", "your", "=?", "?=", "=?UTF-8?B?QQ==?=", "=?UTF-8?B?Y3JlZGl0?=", "x", "PIN"]
    seen = 0
    for _ in range(6000):
        texts = tuple(" ".join(rnd.choice(bits) for _ in range(rnd.randint(1, 3))) for _ in range(rnd.randint(1, 3)))
        sender = " ".join(rnd.choice(bits) for _ in range(rnd.randint(0, 2))) + " <a@b.example>"
        plain = paper_privacy._why_blanks(texts, sender, paper_privacy.DOMAINS, True)
        if plain:
            seen += 1
            assert paper_privacy.why(*texts, sender=sender) == plain, (texts, sender)
    assert seen > 1000, seen
    monkeypatch.setattr(config, "load", lambda: {"paper": "x"})
    assert paper_privacy.why("Hello") == "text that could not be checked" and paper_privacy.private("Hello")
    monkeypatch.setattr(config, "load", lambda: 1 / 0)
    assert paper_privacy.private("Hello", sender="Bob <a@b.example>")
    monkeypatch.undo()
    assert not paper_privacy.private("Hello", sender="Bob <a@b.example>")
    import json
    import pathlib
    import tempfile
    path = pathlib.Path(tempfile.mkdtemp()) / "config.json"
    monkeypatch.setattr(config, "PATH", path)
    assert not paper_privacy.private("Hello", sender="Bob <a@mybank.example>")
    path.write_text(json.dumps({"paper": {"hide_senders": ["mybank.example"]}}))
    assert paper_privacy.why("Hello", sender="Bob <a@mybank.example>") == "domain mybank.example"
    assert not paper_privacy.private("Hello", sender="Bob <a@b.example>")
    import os
    if os.name == "posix" and os.geteuid() != 0:
        path.parent.chmod(0)
        try:
            assert paper_privacy.why("Hello", sender="Bob <a@mybank.example>") == "text that could not be checked"
        finally:
            path.parent.chmod(0o700)
    for broken in ("{not json", "[]", '"x"', json.dumps({"paper": {"hide_senders": "mybank.example"}})):
        path.write_text(broken)
        assert paper_privacy.why("Hello", sender="Bob <a@mybank.example>") == "text that could not be checked", broken
    import logging
    seen = []
    handler = logging.Handler()
    handler.emit = seen.append
    logging.getLogger("paper").addHandler(handler)
    try:
        monkeypatch.setattr(paper_privacy, "_warned", [-60.0])
        monkeypatch.setattr(paper_privacy.time, "monotonic", lambda: 1000.0)
        for _ in range(200):
            assert paper_privacy.private("Hello", sender="Bob <a@b.example>")
        assert len(seen) == 1, len(seen)
        monkeypatch.setattr(paper_privacy.time, "monotonic", lambda: 1061.0)
        assert paper_privacy.private("Hello", sender="Bob <a@b.example>")
        assert len(seen) == 2, len(seen)
    finally:
        logging.getLogger("paper").removeHandler(handler)


def test_encoded_word_headers_are_read_decoded():
    import paper_privacy
    assert paper_privacy.why("=?UTF-8?B?RGVidA==?=") and paper_privacy.why("=?utf-8?Q?Debt_notice?=")
    assert paper_privacy.why("Hello", sender="=?UTF-8?B?RGVidA==?= <a@ok.example>", money=False)
    assert paper_privacy.why("=?UTF-8?B?RGVidA?=") and paper_privacy.why("=?x-nope?B?RGVidA==?=")
    assert not paper_privacy.why("=?UTF-8?B?SGVsbG8gdGhlcmU=?=") and not paper_privacy.why("a =? b", sender="=?UTF-8?B?Qm9i?= <a@ok.example>")


def test_characters_that_print_as_nothing_do_not_split_a_private_word():
    import paper_privacy
    for filler in ("\ufffc", "\ufffd", "\ue000", "\u0378", "\ud800", "\U000f0000"):
        assert paper_privacy.why("de" + filler + "bt"), repr(filler)
        assert paper_privacy.why("hello", sender="de" + filler + "bt <a@ok.example>"), repr(filler)
    assert not paper_privacy.why("hello there")


def test_no_printer_set_means_the_system_default_printer():
    class Run:
        def __init__(self, out):
            self.out = out

        def __call__(self, cmd, **k):
            return type("R", (), {"stdout": self.out, "returncode": 0})()
    assert paper.resolve_printer("", Run("system default destination: Home_Printer\n")) == "Home_Printer"
    assert paper.resolve_printer("", Run("no system default destination\n")) == ""
    assert paper.resolve_printer("Office_Printer", Run("system default destination: Home_Printer\n")) == "Office_Printer"


def test_the_dateline_names_the_place_only_when_one_is_set():
    import paper_render
    d = {"day": NOW.date().isoformat(), "edition": 3}
    assert "<span>FREE TO YOU</span>" in paper_render.flag(d, "print")
    assert "<span>RIVERTON" in paper_render.flag({**d, "place": "Riverton"}, "print")


def test_a_story_sent_as_one_long_block_is_cut_into_paragraphs():
    import paper_articles
    body = " ".join(f"Sentence number {i} says something about the city budget." for i in range(1, 10))
    page = ('<html><head><script type="application/ld+json">' + json.dumps({"@type": "NewsArticle", "articleBody": body})
            + "</script></head><body></body></html>")
    paras = paper_articles.main_text(page)["paras"]
    assert len(paras) == 3 and paras[0].startswith("Sentence number 1 ") and paras[2].endswith("number 9 says something about the city budget.")


def test_the_briefing_page_names_the_week_ahead_only_when_there_are_events():
    import paper_render
    d = {"day": NOW.date().isoformat(), "pages": 4, "events": {"items": []}}
    assert "The week ahead around you" not in paper_render.render(d, "print")
    assert "The week ahead around you ·" in paper_render.render({**d, "events": paper_fake.town_events(NOW.date())}, "print")


def test_paper_model_calls_use_her_live_context_size():
    import ctxsize
    sent = []
    m = paper.Model(post=lambda url, json=None, timeout=None: (sent.append(json), type("R", (), {"json": lambda self: {"message": {"content": "ok"}}})())[1])
    assert m.ask("s", "u") == "ok"
    assert sent[0]["options"]["num_ctx"] == ctxsize.options(m.model)["num_ctx"]


def test_a_day_or_image_name_with_a_trailing_newline_is_refused():
    import paper_api
    assert paper_api._day("2026-11-05") == "2026-11-05"
    assert paper_api._day("2026-11-05\n") is None
    assert paper_api.DAY_RE.fullmatch("2026-11-05\n") is None
    assert paper_api.IMG_RE.fullmatch("img1.jpg") and paper_api.IMG_RE.fullmatch("img1.jpg\n") is None


def test_health_visits_and_hidden_letter_tricks_never_print(monkeypatch):
    import paper_privacy
    for t in ("Dentist at 3 pm", "Appointment with Dr. Alvarez", "dr patel follow up", "Mammogram Tuesday", "Urgent care visit", "Flu shot at the pharmacy",
              "Telehealth call", "Annual physical", "Your pre​scription is ready", "Ｄｅｎｔｉｓｔ reminder", "Sur­gery prep", "Dr. Lee at noon"):
        assert paper_privacy.why(t), t
        assert paper_privacy.private(t), t
    assert paper_privacy.why("Reminder", sender="Dr. Kim <office@clinic.example>")
    for t in ("Dr.Smith", "Dr.\u200bSmith", "Dr: Smith", "Dr/Smith", "Dr, Smith", "Dr-Smith", "dentistry consult", "appointment with Dr", "d\u0435ntist", "D\u03bfctor visit",
              "den\u0301tist", "dent\u0336ist", "d\u034fentist", "d e n t i s t", "d.e.n.t.i.s.t"):
        assert paper_privacy.why(t), t
    for t in ("\u0405SA letter", "\u0397SA", "\u04cfogin alert", "\u0261yno", "doc\u3164tor", "doc\u2800tor", "doc\x08tor", "den\u0442ist", "pass\u0461ord reset", "\u0397\u0405\u0391 card", "\u0405\u0405\u0391 letter",
              "deb\u1d1b due", "\u1d05octor", "loa\u0274", "d\u025bbt", "\u0111octor", "\u00f0octor", "\u0142oan", "d\u0131ntist", "l\u0585an", "\u13a0octor", "\u03b2ank",
              "c\u03bcstody", "\u00dfankruptcy", "\u00dfill due", "\u03a9verdraft", "l\u03a9an", "unempl\u00b5yment", "\u0394rrest",
              "de\u00dft", "\u03a9TP 123456", "Re: \u03a9TP", "I \u03a9WE 500", "c\u00b5stody"):
        assert paper_privacy.why(t), t
    for t in ("Caf\u00e9 lunch", "Draft essay", "R S V P to Sam", "a b c d party", "Stra\u00dfe walk", "\u041f\u0440\u0438\u0432\u0435\u0442 from Anna", "Lunch\nwith Sam",
              "10k\u03a9 resistor", "order 100\u00b5F caps", "5\u00b5m bolt", "\u0394t", "2 M\u03a9", "Stra\u00dfe 5", "Ph\u1edf dinner", "H\u00e0 N\u1ed9i", "\u03c0 day", "5 \u03a9", "Kyiv \u041a\u0438\u0457\u0432"):
        assert not paper_privacy.why(t), (t, paper_privacy.why(t))
    for t in ("Your Amazon order has shipped", "Hard drive arrives Thursday", "Address the club newsletter", "Mail the library form", "Drum lesson at 4"):
        assert not paper_privacy.why(t), (t, paper_privacy.why(t))
    for c in "\u00df" + paper_privacy.UNIT_LETTERS:
        assert c in paper_privacy.READINGS
    for bad in ("EO\u00df", "pa\u00dfword", "reset your pa\u00dfcode", "repo\u00dfession", "t\u0394x due", "V\u0394", "SS\u0394", "\u0394CH", "\u2126TP",
                "RITE \u0394I\u0394", "rite \u0394i\u0394", "compensation \u0394N\u0394 pension", "D\u03bcV", "\u2206ebt", "rite \u2206I\u2206", "\u00df" * 12 + " ok"):
        assert paper_privacy.why(bad), bad
    for fine in ("Stra\u00dfe 5", "Fu\u00dfball", "Wei\u00df Bier", "Ma\u00dfe", "5 \u03a9", "2 M\u03a9", "5\u03bcm", "10 \u03bcs", "\u00b5SD", "\u03bcg/L", "\u0394T 5", "\u0394p 5 \u03bcm", "5 \u03bcF, 10 \u03bcs, 2 M\u03a9, 1 k\u03a9", "Gr\u00fc\u00dfe aus der Stra\u00dfe"):
        assert not paper_privacy.why(fine), fine
    assert paper_privacy.why("hi", sender="RITE \u0394I\u0394 <a@b.example>")
    started = time.time()
    assert paper_privacy.why("x " * 8000 + "\u00df\u0394\u03bc" * 6)
    assert time.time() - started < 2
    for spelled in ("d e \u1e9e t", "d e \u03b2 t", "d.e.\u03b2.t", "b a n \u043a", "\u03b4 e b t"):
        assert paper_privacy.why(spelled), spelled
    for fine in ("\u03b1 \u03b2 \u03b3", "a b c", "x y z are axes"):
        assert not paper_privacy.why(fine), fine
    under = " ".join(["\u00df"] * 6)
    over = " ".join(["\u00df"] * 7)
    assert 4 ** 6 * (len(under) + 1) <= paper_privacy.READINGS_BUDGET < 4 ** 7 * (len(over) + 1)
    for slow in (under, "\u00df " * 3 + "\u03bc " * 3, "\u00df " * 3 + "\u0394 " * 6):
        started = time.time()
        assert not paper_privacy.why(slow), slow
        assert time.time() - started < 1, (slow, time.time() - started)
    assert paper_privacy.why(over) == "word with mixed alphabets"
    for hide in ("\u0394\u0395\u0392\u0166", "see \u0394\u0395\u0392\u0166 now", "\u0394\u0395\u039d\u03a4\u0399\u03a3\u03a4", "\u0394\u0395\u039d\u03a4\u0399\u0166\u03a4"):
        assert paper_privacy.why(hide) == "word with mixed alphabets", hide
    assert paper_privacy.why("hello", sender="\u0394\u0395\u0392\u0166 <a@ok.example>") == "word with mixed alphabets"
    loads = []
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: loads.append(1) or ())
    assert not paper_privacy.why(under)
    assert len(loads) == 1
    print("PASS: health visits, Dr. names and hidden-letter spellings are kept off the paper")


def test_a_text_prints_only_when_every_reading_of_it_prints(monkeypatch):
    import itertools
    import random
    import unicodedata
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    rng = random.Random(1115)
    pieces = list(paper_privacy.READINGS) + ["\u2206", "\u0166", "\u0395", "\u0392", "\u039d", "\u03a4", "\u0399", "\u03a3", "\u210f", "\u1e9e", "e", "b", "t", "a", "n", "k", "E", "B", "T", "I", "S", " ", " ", ".", "-", "1"]
    printed = 0
    for _ in range(20000):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 7)))
        if paper_privacy.why(text):
            continue
        printed += 1
        flat = unicodedata.normalize("NFKD", text.replace("\u2206", "\u0394"))
        spots = [i for i, c in enumerate(flat) if c in paper_privacy.READINGS]
        for combo in itertools.product(*(paper_privacy.READINGS[flat[i]] for i in spots)):
            chars = list(flat)
            for i, r in zip(spots, combo):
                chars[i] = r
            reading = "".join(chars)
            assert not paper_privacy.why(reading), (text, reading, paper_privacy.why(reading))
    assert printed > 500, printed
    print("PASS: a text prints only when every reading of its unit letters prints too")


def test_whatever_the_old_single_reading_hid_is_still_hidden():
    import random
    import paper_privacy
    glued = ("caf\u00e9bank", "Zo\u00e9loan", "abc\u200bbank", "foo\x0bbank", "foo\x0cdebt", "debt\u00e9", "\u00e9pay", "\u00e1debt",
             "VA\u2060lo", "ref\u200bIRS", "SSA\u200bref ta", "pin\u200b1234", "loan\uff44\uff45\uff42\uff54", "x\u00adbank", "a\u0301bank", "foo\x85debt")
    for text in glued:
        assert paper_privacy.why(text, money=False), repr(text)
    assert paper_privacy.why("lunch", sender="A <a@x.example> \u200bbank", money=False)
    rng = random.Random(23)
    marks = ["\u200b", "\u200c", "\u2060", "\ufeff", "\u00ad", "\x0b", "\x0c", "\x1f", "\x85", "\u0301", "\u00e9", "\u00e1", "\uff44", "\uff42", " ", "", "-", "=?", "\u0394", "\u2206"]
    words = ["bank", "debt", "loan", "IRS", "SSA", "VA", "pin 1234", "code 55512", "$5", "credit", "card", "lunch", "ref", "abc", "Zo", "ta"]
    hits = 0
    for _ in range(6000):
        texts = tuple("".join(rng.choice(words) + rng.choice(marks) for _ in range(rng.randrange(1, 4))) for _ in range(rng.randrange(1, 4)))
        sender = rng.choice(["", "Bob <b@ok.example>", "A" + rng.choice(marks) + "bank <a@x.example>", "Pat <p@sub.va.gov>"])
        money = rng.random() < 0.5
        old = paper_privacy._first(tuple(t for t in texts if t), sender, paper_privacy.DOMAINS, money)
        if old:
            hits += 1
            assert paper_privacy.why(*texts, sender=sender, money=money) == old, (texts, sender)
    assert hits > 1000, hits
    assert paper_privacy.why("card =? credit", money=False) == "word credit card"
    for text in ("=?iso-8859-8-i?Q?Past_Due_notice?=", "=?windows-874?Q?Late_fee_added?=", "=?x-sjis?Q?=70ast_due?=", "=?x-sjis?B?cGFzdCBkdWU=?=", "=?x-sjis?B?ZGVi?= =?x-sjis?B?dA==?="):
        assert paper_privacy.why(text, money=False).startswith("word "), text
    assert not paper_privacy.why("=?x-sjis?Q?Lunch_on_Friday?=", money=False)
    assert paper_privacy.why("=?x-foo?B?ZGVi?dA==?=", money=False) == "word debt"
    for gap in ("  ", "\n\n", " \t ", "\u2028", "\u2029", "\u1680", "\x0b", "\x0c", "\x1c", "\x85", "\u200b", "\u00ad", "\u2060", "\ufeff", " \u200b "):
        for phrase in ("past due", "late fee", "wells fargo", "credit card", "capital one"):
            text = "Your " + phrase.replace(" ", gap) + " notice"
            assert paper_privacy.why(text, money=False).startswith("word "), repr(text)
    assert paper_privacy.why("p\u0430st  due", money=False)
    assert not paper_privacy.why("Lunch  on\u2028Friday\u200b at  noon", money=False)
    assert paper_privacy._first(("hi",), "A <a@chase.com>", paper_privacy.DOMAINS, False) == "domain chase.com"
    assert paper_privacy._first(("it was $5",), "", (), True) == "an amount of money" and not paper_privacy._first(("it was $5",), "", (), False)
    import time
    started = time.monotonic()
    for text in ("\ufdfa" * 100000 + "\ufffd" * 100000, "\ufdfa\ufffd" * 100000, "lunch " * 40000):
        assert paper_privacy.why(text, money=False) == "text too long to check"
    assert paper_privacy.why("x" * 200000 + " bank", money=False) == "word bank"
    assert paper_privacy.why("\ufdfa" * (paper_privacy.TOO_LONG - 30) + "\u00df" * 30, money=False)
    assert not paper_privacy.why("lunch " * 600, money=False)
    assert paper_privacy.why(*["q\u00dfq  x"] * 3 + ["\ufffd"] * 1465 + ["=?x-foo?Q?lunch?="], money=False) == "text too long to check"
    assert paper_privacy.why(*["q\u00dfq  x"] * 3 + ["\ufffd " * 60] * 36 + ["=?x-foo?Q?lunch?="], money=False) is not None
    assert not paper_privacy.why(*["lunch"] * 40, money=False)
    assert time.monotonic() - started < 10
    assert not paper_privacy.why("Lunch on Friday", sender="Bob <b@ok.example>")
    print("PASS: the old single reading still hides everything it hid")


def test_hidden_characters_and_odd_spacing_never_unhide_a_private_phrase(monkeypatch):
    import base64
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    word = lambda t: "=?utf-8?B?" + base64.b64encode(t.encode()).decode() + "?="
    for mark in ("͏", "️", "⁦", "؜", "᠎", "\U000e0020", "￹", "ㅤ", "​͏"):
        for phrase in ("past due", "late fee", "wells fargo", "credit card", "capital one"):
            for gap in (" " + mark + " ", mark + " ", " " + mark):
                text = "Your " + phrase.replace(" ", gap) + " notice"
                assert paper_privacy.why(text, money=False) in ("word " + phrase, "word with mixed alphabets"), repr(text)
                assert paper_privacy.why(word(text), money=False) in ("word " + phrase, "word with mixed alphabets"), repr(text)
        assert paper_privacy.why("d " + mark + " e " + mark + " b " + mark + " t", money=False) in ("word debt", "word with mixed alphabets"), repr(mark)
    for mark in ("⁦", "\U000e0020", "￹", "ㅤ", "\x01"):
        assert paper_privacy.why("Your past" + mark + "due notice", money=False) in ("word past due", "word with mixed alphabets"), repr(mark)
    for text in ("d  e  b  t", "d \n e\tb  t", "d\n\ne\n\nb\n\nt"):
        assert paper_privacy.why(text, money=False) == "word debt", repr(text)
    for text in ("past  due", "late\r\nfee", "d  e  b  t", "past​due"):
        assert paper_privacy.why(word(text), money=False).startswith("word "), repr(text)
    assert paper_privacy.why("=?utf-8?Q?past__due?=", money=False) == "word past due"
    assert paper_privacy.why("=?utf-8?B? =?utf-8?B?cGFzdCBkdWU=?=", money=False)
    assert paper_privacy.why("=?bogus?B?eA==?= =?utf-8?B?cGFzdCBk\ndWU=?=", money=False) == "word past due"
    for text in ("Lunch ͏ on⁦Friday at  noon", "café at 5", "Meet​ing notes", word("Lunch  at\r\nnoon")):
        assert not paper_privacy.why(text, money=False), repr(text)
    assert paper_privacy.why("pa\u200bst \u034f due", money=False) == "word past due"
    assert paper_privacy.why("d e b t  o k a y", money=False) == "word debt"
    assert not paper_privacy.why("re\u0301sume\u0301 for the cafe\u0301", money=False)
    calls = []
    inner = paper_privacy._words_in
    monkeypatch.setattr(paper_privacy, "_words_in", lambda *a: calls.append(1) or inner(*a))
    assert not paper_privacy.why("Lunch at noon", money=False) and calls == [1], calls
    print("PASS: hidden characters, odd spacing, and encoded words with either still hide a private phrase")


def test_text_over_the_caps_gets_one_reading_only(monkeypatch):
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    calls = []
    first = paper_privacy._first
    monkeypatch.setattr(paper_privacy, "_first", lambda *a: calls.append(1) or first(*a))
    assert paper_privacy.why("a​ " * 2000, money=False) == "text too long to check"
    assert paper_privacy.why(*["a"] * 41, money=False) == "text too long to check"
    assert calls == [1, 1], calls
    assert paper_privacy.why("past  due " * 500, money=False) == "text too long to check"
    assert paper_privacy.why("past due " * 500, money=False) == "word past due"
    print("PASS: text over the length or field cap gets the old reading only, then stays off the paper")


def test_encoded_words_that_cannot_be_read_never_print(monkeypatch):
    import base64
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    b64 = lambda t: base64.b64encode(t.encode()).decode()
    cannot = "text that could not be checked"
    due = b64("past due")
    cases = ["=?utf-8?B?%s =?utf-8?B?%s?=" % (pre, due) for pre in ("aGVsbG8", "zz", "aGk")]
    cases += ["=?utf-8?Q?hello =?utf-8?Q?past_due?=", "=?utf-8?B?aGVsbG8 =?utf-8?Q?past_due?=", "Re: =?utf-8?B?aGVsbG8 =?utf-8?B?%s?= ok" % due,
              "=?utf-8?B?aGVsbG8\t=?utf-8?B?%s?=" % due, "=?utf-8?B?aGVsbG8\n=?utf-8?B?%s?=" % due, "=?utf-8?B?%s?= =?" % due + "x?B?",
              "=?utf-8?B?" + b64("=?utf-8?B?" + due + "?=") + "?=", "=??B?%s?=" % due, "=?utf 8?B?%s?=" % due]
    for text in cases:
        assert paper_privacy.why(text, money=False) == cannot, repr(text)
        assert paper_privacy.why("hi", sender=text + " <a@ok.example>", money=False) == cannot, repr(text)
        assert paper_privacy.why("hi", text, money=False) == cannot, repr(text)
    assert paper_privacy.why("=?utf-8?B?%s?=" % due, money=False) == "word past due"
    assert paper_privacy.why("hi", sender="=?utf-8?B?%s?= <a@ok.example>" % due, money=False) == "word past due"
    assert paper_privacy.why("=?utf-8?B?%s?= =?utf-8?B?%s?=" % (b64("pa"), b64("st due")), money=False) == "word past due"
    assert paper_privacy.why("pa=?utf-8?B?%s?=" % b64("st due"), money=False) == "word past due"
    assert paper_privacy.why("x=?utf-8?B?%s?= y" % b64("debt"), money=False) == "word debt"
    assert paper_privacy.why("=?utf-16?B?%s?=" % due, money=False) == "word past due"
    assert not paper_privacy.why("=?utf-8?B?%s?=" % b64("Lunch at noon"), money=False)
    assert not paper_privacy.why("Re: =?utf-8?Q?Lunch_at_noon?= ok", "a =? b", money=False)
    assert not paper_privacy.why("hi", sender="=?utf-8?B?%s?= <a@ok.example>" % b64("Bob"), money=False)
    assert not hasattr(paper_privacy, "ENCODED_AT_RE")
    print("PASS: an encoded word that cannot be read stays off the paper, readable ones are read")


def test_work_budget_and_normalized_length_cap_are_deterministic(monkeypatch):
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    long = "text too long to check"
    pre = "=?utf-8?B?aGk=?=  \ufffc  "
    for text in ((pre + "\u00df ; " + "$ " * 4000)[:3000], (pre + "\u00df ; " + "a-b-c-d-e-f-g" * 4000)[:3000]):
        assert paper_privacy.why(text) == long
        assert paper_privacy._spent.n > paper_privacy.WORK_BUDGET
    assert paper_privacy.why("\ufdfa" * 230) == long and paper_privacy.why("\ufdfa" * 3900) == long
    assert paper_privacy.why("\ufdfa" * 100, sender="\ufdfa" * 123) == long
    assert paper_privacy.why("\ufdfa" * 100, sender="\ufdfa" * 100) != long
    plain = "The quick brown fox jumps over the lazy dog. " * 100
    assert paper_privacy.why(plain[:4000]) == "" and paper_privacy._spent.n == 14984 <= paper_privacy.WORK_BUDGET // 8
    word = "=?utf-8?B?" + base64.b64encode(b"Lunch at noon").decode() + "?="
    mixed = ("Die stra\u00dfe is 5 \u03bcm wide, \u0394T  small. " + word + " " + "Meet at the cafe on Friday and bring the notes for the project, ok? " * 10)[:500]
    assert paper_privacy.why(mixed, money=False) != long and paper_privacy._spent.n == 116548 < paper_privacy.WORK_BUDGET * 4 // 5
    assert paper_privacy.why("a" * 4000) == "" and paper_privacy.why("a" * 4001) == long
    assert paper_privacy.why("a" * 3000, sender="b" * 1000) == "" and paper_privacy.why("a" * 3000, sender="b" * 1001) == long
    assert paper_privacy.why(*["a"] * 40) == "" and paper_privacy.why(*["a"] * 41) == long
    assert paper_privacy.why("Lunch at noon") == ""
    used = paper_privacy._spent.n
    assert used > 0
    assert paper_privacy.WORK_BUDGET == 150000
    assert paper_privacy.why("Lunch at noon", sender="Bob Smith") == "" and paper_privacy._spent.n == 68
    monkeypatch.setattr(paper_privacy, "WORK_BUDGET", used - 1)
    assert paper_privacy.why("Lunch at noon") == long
    monkeypatch.setattr(paper_privacy, "WORK_BUDGET", used)
    assert paper_privacy.why("Lunch at noon") == ""
    print("PASS: the length cap counts the normalized text and sender, and a fixed work budget bounds the cost")


def test_combining_marks_never_become_a_space_but_other_hidden_characters_do():
    import paper_privacy
    for text in ("d\u0155\u017ea\u0165", "d\u0155\u017ea", "Ako d\u0155\u017ea\u0165 loptu", "caf\u00e9 r\u00e9sum\u00e9"):
        assert not paper_privacy.why(text, money=False), repr(text)
    assert paper_privacy.why("dr\u00adive", money=False)
    assert paper_privacy.why("past\u200bdue", money=False) == "word past due"
    plain = paper_privacy._plain
    assert plain("a\u0301b", " ") == "ab" and plain("a\u0301b", "") == "ab" and plain("a\u0301b") == "ab"
    assert plain("a\u0903b", " ") == "ab" and plain("a\u20ddb", " ") == "ab"
    assert plain("a\u200bb", " ") == "a b" and plain("a\u0001b", " ") == "a b" and plain("a\u3164b", " ") == "a b"
    assert plain("a\u200bb", "") == "ab" and plain("a\u200bb") == "ab"
    assert plain("a\u200b\u200bb", " ") == "a b" and plain("a  b", "") == "a b" and plain("a  b") == "a  b"
    assert plain("a\nb", "") == "a b" and plain("a\tb", "") == "a b" and plain("a\rb") == "a b"
    print("PASS: combining marks are removed in every reading, other hidden characters still split words")


def test_privacy_reads_values_that_are_not_strings_as_text_before_any_emptiness_test(monkeypatch):
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())

    class Falsy:
        def __init__(self, text):
            self.text = text

        def __bool__(self):
            return False

        def __str__(self):
            return self.text

    class Empty(str):
        def __len__(self):
            return 0

        def __iter__(self):
            return iter(())

    class Renamed(str):
        def __str__(self):
            return "ok"

    class Broken:
        def __str__(self):
            raise ValueError("no text")

    assert paper_privacy.why(Falsy("past due")) == "word past due"
    assert paper_privacy.why("hi", sender=Falsy("Bob <a@chase.com>")) == "domain chase.com"
    assert paper_privacy.why(Empty("past due")) == "word past due" and paper_privacy.why("hi", sender=Empty("x@chase.com"))
    assert paper_privacy.why(Renamed("past due")) == "word past due"
    assert paper_privacy.why(b"past due", ["debt"], 7)
    assert paper_privacy.why(Broken()) == "text that could not be checked"
    assert paper_privacy.why("hi", sender=Broken()) == "text that could not be checked"
    assert not paper_privacy.why(Falsy("lunch"), Falsy(""), 0, sender=Falsy(""), money=False)
    print("PASS: every text and the sender is turned into a string before it is tested for being empty")


def test_a_sender_name_with_a_hidden_character_and_the_first_reading_layers_still_hide(monkeypatch):
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    assert paper_privacy.why("hi", sender="Past \u200b due <a@ok.com>") == "word past due"
    assert paper_privacy.why("hi", sender="=?utf-8?B?cGFzdCBkdWU=?= <a@ok.com>") == "word past due"
    assert paper_privacy.why("past=?utf-8?B?ZHVl?=", money=False) == "word past due"
    assert paper_privacy.why("=?cp037?B?l4Gio0CEpIU=?=", money=False) == "word past due"
    assert paper_privacy.why("=?utf-8?B?cGFzdA==?=due", money=False) == "word past due"
    assert paper_privacy.why("=?utf-8?Q?accou=6et_ending?=", money=False) == "word account ending"
    assert paper_privacy.why("=?utf-8?B?/w==?= lunch", money=False) == ""
    assert paper_privacy.why("hi", sender="=?utf-8?B?Qm9iIDxhQGNoYXNlLmNvbT4=?=") == "domain chase.com"
    assert paper_privacy.why("hi", sender="Past\u200b due <a@ok.com>") == "word past due"
    assert not paper_privacy.why("hi", sender="Bob \u200b Smith <a@ok.com>")
    monkeypatch.setattr(paper_privacy, "_why_read", lambda *a: "")
    for text in ("past\u200bdue", "past\n\ndue", "past\u00ad\u00addue", "past  due"):
        assert paper_privacy._why_all((text,), "", paper_privacy.DOMAINS, False) == "word past due", repr(text)
    assert paper_privacy._why_all(("lunch",), "", paper_privacy.DOMAINS, False) == ""
    print("PASS: a hidden character in a sender name and the first readings still hide a private phrase")


def _quoted(raw, charset, kind, rng):
    if kind == "B":
        return "=?" + charset + "?B?" + base64.b64encode(raw).decode() + "?="
    body = "".join(chr(b) if chr(b).isascii() and chr(b).isalnum() and rng.random() < 0.7 else "_" if b == 32 and rng.random() < 0.5 else "=%02X" % b for b in raw)
    return "=?" + charset + "?Q?" + body + "?="


def _encoded_field(rng, phrases, plains):
    parts = []
    for _ in range(rng.randrange(1, 4)):
        if parts and rng.random() < 0.7:
            parts.append(rng.choice(plains))
        phrase = rng.choice(phrases)
        charset = rng.choice(("utf-8", "utf-8", "iso-8859-1", "koi8-r", "utf-16-le", "windows-1252", "utf-16", "UTF-8*en"))
        try:
            raw = phrase.encode(charset.split("*")[0])
        except UnicodeEncodeError:
            charset, raw = "utf-8", phrase.encode()
        cuts = sorted({rng.randrange(1, len(raw)) for _ in range(rng.randrange(0, 4))} if len(raw) > 1 else set())
        words = [_quoted(raw[a:b], charset, rng.choice("BbQq").upper(), rng) for a, b in zip([0] + cuts, cuts + [len(raw)])]
        gaps = [rng.choice((" ", " ", "\r\n ", "\t", "  ", "", "\n\n", "\x85", "\t\t")) for _ in words[1:]] + [""]
        parts.append("".join(w + g for w, g in zip(words, gaps)))
        if rng.random() < 0.4:
            parts.append(rng.choice(plains))
    return rng.choice((" ", "  ", "")).join(parts).strip()


def _client_reading(text):
    import email.header
    try:
        return str(email.header.make_header(email.header.decode_header(text)))
    except Exception:
        return None


def test_a_field_hides_whenever_any_reading_of_it_hides_as_plain_text(monkeypatch):
    import random
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    phrases = ["debt", "bank", "venmo", "v e n m o", "d e b t", "ｐａｙｐａｌ", "chase", "past due", "code 12345", "Señor 98765 T o m pin",
               "lunch", "hello", "été", "дело", "pin", "98765", "5pm", "débít"]
    plains = ["hello", "ok", "Re:", "lunch at 5pm", "98765", "pin", "T o m", "x", "(c)", "é"]
    rng = random.Random(2815)
    checked = hidden = 0
    for _ in range(160):
        text = _encoded_field(rng, phrases, plains)
        sender = _encoded_field(rng, phrases, plains) + " <a@b.example>" if rng.random() < 0.4 else ""
        both = bool(sender)
        text_readings = {_client_reading(text), *paper_privacy._readings(text)} - {None}
        sender_readings = ({_client_reading(sender), *paper_privacy._readings(sender)} - {None}) if both else {""}
        got = paper_privacy.why(text, sender=sender)
        checked += 1
        hidden += bool(got)
        for rt in text_readings:
            for rs in sender_readings:
                if paper_privacy.why(rt, sender=rs):
                    assert got, (text, sender, rt, rs)
    assert checked == 160 and hidden > 40, (checked, hidden)
    print("PASS: an encoded field hides whenever any one reading of it hides as plain text")


def test_adjacent_encoded_words_are_joined_by_the_mail_client_and_checked_that_way():
    import paper_privacy
    q = lambda t: "=?utf-8?q?" + t + "?="
    assert paper_privacy.why(q("de") + " " + q("bt") + " ok " + q("5pm"), money=False) == "word debt"
    assert paper_privacy.why(q("de") + " " + q("bt") + " ok " + q("5") + " " + q("pm"), money=False) == "word debt"
    assert paper_privacy.why(q("ven") + " " + q("mo") + " hi " + q("x"), money=False) == "word venmo"
    assert paper_privacy.why(q("x") + " hi " + q("ven") + "\r\n " + q("mo"), money=False) == "word venmo"
    assert paper_privacy.why("hi", sender=q("de") + " " + q("bt") + " ok <a@b.example>", money=False) == "word debt"
    assert not paper_privacy.why(q("lun") + " " + q("ch") + " ok " + q("5pm"), money=False)
    print("PASS: encoded words that a mail client joins are read joined next to plain text")


def test_two_readings_of_one_field_are_never_glued_into_one_text():
    import paper_privacy
    spelled = "=?utf-8?B?" + base64.b64encode(b"v e n m o").decode() + "?="
    assert paper_privacy.why(spelled + " hello", money=False) == "word venmo"
    assert paper_privacy.why("hello " + spelled, money=False) == "word venmo"
    assert paper_privacy.why("hello", sender=spelled + " <a@b.example>", money=False) == "word venmo"
    assert not paper_privacy.why("=?utf-8?B?" + base64.b64encode(b"v e n m").decode() + "?= hello", money=False)
    assert paper_privacy.why("=?utf-8?B?" + base64.b64encode(b"d e b t").decode() + "?= ok", money=False) == "word debt"
    assert all(" ".join(r.split()).count("v e n m o") <= 1 for r in paper_privacy._readings(spelled + " hello"))
    assert len(paper_privacy._readings(spelled + " hello")) > 1
    print("PASS: the readings of one field are checked one by one and never glued into one text")


def test_a_character_split_across_two_encoded_words_is_read_whole():
    import paper_privacy
    raw = "ｄｅｂｔ".encode()
    for cut in range(1, len(raw)):
        words = ["=?utf-8?B?" + base64.b64encode(raw[:cut]).decode() + "?=", "=?utf-8?B?" + base64.b64encode(raw[cut:]).decode() + "?="]
        assert paper_privacy.why(" ".join(words), money=False) == "word debt", cut
        assert paper_privacy.why("hello", sender="\r\n ".join(words) + " <a@b.example>", money=False) == "word debt", cut
    raw = "bаnk".encode()
    for cut in range(1, len(raw)):
        assert paper_privacy.why("=?utf-8?B?" + base64.b64encode(raw[:cut]).decode() + "?= =?utf-8?B?" + base64.b64encode(raw[cut:]).decode() + "?=", money=False)
    assert not paper_privacy.why("=?utf-8?B?" + base64.b64encode("été".encode()[:1]).decode() + "?= =?utf-8?B?" + base64.b64encode("été".encode()[1:]).decode() + "?=", money=False)
    print("PASS: a character cut between two encoded words is rebuilt before the check")


def test_a_decoded_reading_gets_the_same_first_check_as_plain_text():
    import paper_privacy
    text = "=?utf8?q?=ef=BD=90=EF=bd=81=ef=BD=99=EF=BD=90=ef=bd=81=ef=BD=8c?=\n =?iso-8859-1?q?=63has?=\t=?iso-8859-1?q?=65?="
    assert paper_privacy.why(text, money=False) == "word chase"
    assert paper_privacy.why("ｐａｙｐａｌ" + "chase", money=False) == "word chase"
    assert paper_privacy.why("hello", sender="=?utf-8?B?" + base64.b64encode("ｐａｙ ｐａｌ".encode()).decode() + "?= <a@b.example>", money=False) == "word pay"
    assert not paper_privacy.why("=?utf-8?B?" + base64.b64encode("ｈｅｌｌｏ".encode()).decode() + "?=", money=False)
    print("PASS: a decoded reading goes through the first check as well")


def test_readings_of_one_field_do_not_make_a_security_code_with_each_other():
    import paper_privacy
    coded = "=?utf-8?B?" + base64.b64encode("Señor 98765 T o m pin".encode()).decode() + "?="
    assert not paper_privacy.why("hi " + coded)
    assert not paper_privacy.why(coded + " hi")
    assert not paper_privacy.why(coded)
    assert paper_privacy.why("Señor 98765 T o m pin") == ""
    assert paper_privacy.why("pin 98765 =?utf-8?q?x?=") == "a security code"
    assert paper_privacy.why("=?utf-8?q?pin_98765?=") == "a security code"
    assert paper_privacy.why("pin", "98765 " + "=?utf-8?q?x?=") == "a security code"
    print("PASS: two readings of one encoded field never meet to make a security code")


def test_a_base64_word_with_three_pad_signs_could_not_be_checked():
    import paper_privacy
    assert paper_privacy.why("=?utf-8?b?ZGVidA===?=", money=False) == "text that could not be checked"
    assert paper_privacy.why("hello", sender="=?utf-8?b?ZGVidA====?= <a@b.example>", money=False) == "text that could not be checked"
    assert paper_privacy.why("=?utf-8?b?ZGVidA==?=", money=False) == "word debt"
    print("PASS: a base64 word with too many pad signs is kept off the paper as unchecked")


def test_a_quoted_printable_word_with_a_question_mark_in_it_could_not_be_checked():
    import paper_privacy
    assert paper_privacy.why("=?utf-8?q?de?bt?=", money=False) == "text that could not be checked"
    assert paper_privacy.why("hello", sender="=?utf-8?q?de?bt?= <a@b.example>", money=False) == "text that could not be checked"
    assert paper_privacy._word(paper_privacy.ENCODED_RE.search("=?utf-8?q?deb\nt?=")).replace("\n", "") == "debt"
    assert paper_privacy.why("=?utf-8?q?lun?ch?=", money=False) == "text that could not be checked"
    print("PASS: a quoted-printable word with a question mark in its body is kept off the paper as unchecked")


def test_an_unknown_charset_is_read_as_utf_8_in_every_reading():
    import paper_privacy
    word = paper_privacy.ENCODED_RE.search("=?x-foo?q?d=65bt?=")
    assert paper_privacy._word(word) == "debt"
    assert paper_privacy._word(paper_privacy.ENCODED_RE.search("=?x-foo?B?ZGVidA==?=")) == "debt"
    assert paper_privacy._client("=?x-foo?q?d=65bt?=") == "debt"
    assert paper_privacy._client("=?x-foo?q?d=65?= =?x-foo?q?bt?= ok") == "debt ok"
    assert paper_privacy._client("=?x-foo?q?d=65bt?= and =?utf-8?q?ok?=") == "debt and ok"
    assert paper_privacy.why("=?x-foo?q?d=65bt?=", money=False) == "word debt"
    assert paper_privacy.why("hello", sender="=?x-foo?q?d=65bt?= <a@b.example>", money=False) == "word debt"
    assert paper_privacy.why("=?x-foo?q?d=65?= =?x-foo?q?bt?= ok", money=False) == "word debt"
    print("PASS: an unknown charset label is read as utf-8 whatever the reading")


def test_blanks_inside_a_base64_body_are_ignored():
    import paper_privacy
    word = paper_privacy.ENCODED_RE.search("=?utf-8?B?ZGVi dA==?=")
    assert paper_privacy._word(word) == "debt"
    assert paper_privacy._word(paper_privacy.ENCODED_RE.search("=?utf-8?B?ZG\r\nVi\tdA==?=")) == "debt"
    assert paper_privacy.why("=?utf-8?B?ZGVi dA==?=", money=False) == "word debt"
    assert paper_privacy.why("=?utf-8?B?ZGVi\r\n dA==?=", money=False)
    assert paper_privacy._word(paper_privacy.ENCODED_RE.search("=?utf-8?B?ZGVidA==?=")) == "debt"
    assert paper_privacy._word(paper_privacy.ENCODED_RE.search("=?utf-8?B?ZGVi dA?=")) == "debt"
    assert paper_privacy.why("=?utf-8?B?ZGVi dA?=", money=False) == "word debt"
    assert paper_privacy.why("hello", sender="=?utf-8?B?ZGV idA?= <a@b.example>", money=False) == "word debt"
    print("PASS: blanks inside a base64 body do not change what it says")


def test_a_collapsed_whitespace_reading_of_encoded_words_is_checked_too():
    import paper_privacy
    text = "=?utf-8?B?Yg==?=\t\t=?utf-8?B?w58=?=\t\t=?utf-8?B?bg==?=\t\t=?utf-8?B?aw==?="
    assert paper_privacy.why(text, money=False) == "word with mixed alphabets"
    assert paper_privacy.why("ok\x0b=?utf-8?B?Yg==?=\x85=?utf-8?B?w58=?=\x85=?utf-8?B?bg==?=\x85=?utf-8?B?aw==?=", money=False) == "word with mixed alphabets"
    assert not paper_privacy.why("=?utf-8?B?bA==?=\t\t=?utf-8?B?dQ==?=\t\t=?utf-8?B?bg==?=\t\t=?utf-8?B?Y2g=?=", money=False)
    print("PASS: the whitespace-collapsed reading of encoded words is checked as well")


def test_a_mail_client_reading_longer_than_the_input_is_too_long_to_check():
    import paper_privacy
    text = "ab " * 1326 + "=?utf-8?Q?a?=" + "\ud800" * 5 + "ab "
    assert len(text) <= paper_privacy.TOO_LONG
    readings = paper_privacy._readings(text)
    assert max(map(len, readings)) > paper_privacy.TOO_LONG >= max(map(len, readings[:-1]))
    assert paper_privacy.why(readings[-1], money=False) == "text too long to check"
    assert paper_privacy.why(text, money=False) == "text too long to check"
    assert not paper_privacy.why("ab " * 1326 + "=?utf-8?Q?a?=", money=False)
    print("PASS: a reading that is too long to check hides the encoded input")


def test_the_whitespace_collapsed_form_of_every_combination_is_checked():
    import paper_privacy
    text = "Δ\t=?utf-8*en?q?S?==?UTF-8?Q?=ff=fe=53==\r\n00?=\t=?iso-8859-1?q?=00=49?="
    readings = paper_privacy._readings(text)
    assert any(paper_privacy.why(r, money=False) for r in readings)
    assert paper_privacy.why(text, money=False)
    print("PASS: the collapsed form of each combination is checked as plain text")


LANG_TAGGED = (
    ("=?utf-7*en?Q?+AGQAZQBiAHQ-?=", "debt", "word debt"),
    ("=?koi8-r*ru?Q?b=C1nk?=", "b\u0430nk", "word with mixed alphabets"),
    ("=?cp037*en?Q?=84=85=82=A3?=", "debt", "word debt"),
    ("=?iso-8859-1*de?Q?=DFill_due?=", "\u00dfill due", "word with mixed alphabets"),
    ("=?iso-8859-5*ru?Q?d=D5bt?=", "d\u0435bt", "word with mixed alphabets"),
    ("=?utf-16*en?B?ZABlAGIAdAA=?=", "debt", "word debt"),
    ("=?utf-7*en*fr?Q?+AGQAZQBiAHQ-?=", "debt", "word debt"),
    ("=?utf-7*?Q?+AGQAZQBiAHQ-?=", "debt", "word debt"),
)


def _policy_reading(text):
    import email.policy
    msg = email.message_from_string("Subject: " + text + "\n\nx", policy=email.policy.default)
    return str(msg["Subject"])


def test_a_language_tagged_charset_is_read_with_its_real_codec(monkeypatch):
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    assert paper_privacy._known("utf-7*en") and paper_privacy._known("koi8-r*ru*x") and paper_privacy._known("utf-8")
    assert not paper_privacy._known("x-foo*en") and not paper_privacy._known("*en")
    for field, shown, reason in LANG_TAGGED:
        assert _policy_reading(field) == shown, field
        assert paper_privacy._word(paper_privacy.ENCODED_RE.search(field)) == shown, field
        assert paper_privacy._client(field) == shown, field
        assert shown in paper_privacy._readings(field), field
        assert paper_privacy.why(field, money=False) == reason, field
        assert paper_privacy.why("hello", sender=field + " <a@b.example>", money=False) == reason, field
        assert paper_privacy.why("Re: " + field + " ok", money=False) == reason, field
    assert paper_privacy._client("=?koi8-r*ru?Q?b=C1?= =?utf-8*en?Q?nk?=") == _policy_reading("=?koi8-r*ru?Q?b=C1?= =?utf-8*en?Q?nk?=")
    assert not paper_privacy.why("=?utf-8*en?Q?Lunch_at_noon?=", money=False)
    assert not paper_privacy.why("=?iso-8859-1*de?Q?Gr=FC=DFe_aus_Berlin?=", money=False)
    assert not paper_privacy.why("=?utf-7*en?Q?+AEgAZQBsAGwAbw-?=", money=False)
    print("PASS: a language tag on the charset is dropped and the real codec reading is checked")


def _tagged_field(rng, phrases, plains):
    codecs_ = ("utf-7", "utf-16", "utf-16le", "cp037", "koi8-r", "iso-8859-5", "iso-8859-1", "utf-8", "ascii")
    tags = ("", "*en", "*ru", "*", "*en*fr", "*x-y")
    parts = []
    for _ in range(rng.choice([1, 1, 2, 3])):
        phrase = rng.choice(phrases) if rng.random() < 0.6 else rng.choice(plains)
        codec = rng.choice(codecs_)
        try:
            raw = phrase.encode(codec)
        except UnicodeEncodeError:
            codec, raw = "utf-8", phrase.encode("utf-8")
        label = codec + rng.choice(tags)
        if rng.random() < 0.5:
            parts.append("=?%s?B?%s?=" % (label, base64.b64encode(raw).decode()))
        else:
            parts.append("=?%s?Q?%s?=" % (label, "".join("=%02X" % b for b in raw)))
        if rng.random() < 0.3:
            parts.append(rng.choice(plains))
    return rng.choice([" ", "", "\t"]).join(parts)


def test_a_field_with_language_tagged_charsets_hides_whenever_any_reading_hides(monkeypatch):
    import random
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    phrases = ["debt", "bank", "b\u0430nk", "d\u0435bt", "\u00dfill due", "past due", "venmo", "v\u0435nmo", "code 12345", "chase", "d e b t"]
    plains = ["hello", "ok", "Re:", "lunch", "5pm", "x"]
    rng = random.Random(2916)
    checked = hidden = real_only = 0
    for _ in range(200):
        text = _tagged_field(rng, phrases, plains)
        sender = _tagged_field(rng, phrases, plains) + " <a@b.example>" if rng.random() < 0.4 else ""
        got = paper_privacy.why(text, sender=sender)
        checked += 1
        hidden += bool(got)
        text_readings = {_policy_reading(text), *paper_privacy._readings(text)}
        sender_readings = {_policy_reading(sender), *paper_privacy._readings(sender)} if sender else {""}
        for rt in text_readings:
            for rs in sender_readings:
                if paper_privacy.why(rt, sender=rs):
                    assert got, (text, sender, rt, rs)
        if paper_privacy.why(_policy_reading(text)) and paper_privacy.why(text.replace("*", "")) != paper_privacy.why(text):
            real_only += 1
    assert checked == 200 and hidden > 60 and real_only > 0, (checked, hidden, real_only)
    print("PASS: a field with language-tagged charsets hides whenever any reading of it hides as plain text")


def test_the_combination_cap_is_exact(monkeypatch):
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    assert paper_privacy.MAX_COMBOS == 48
    six = "a=?latin-1?Q?=E9?=b"
    six_debt = "a=?utf-7?Q?+AGQAZQBiAHQ-?=b"
    seven = "a=?latin-1?Q?=E9?==?latin-1?Q?=E9?=a"
    two = ["=? x", "=? y", "=? z"]
    sizes = [len(paper_privacy._readings(f)) for f in (six, six_debt, seven, *two)]
    assert sizes == [6, 6, 7, 2, 2, 2]
    calls = []
    real = paper_privacy._why_combo
    monkeypatch.setattr(paper_privacy, "_why_combo", lambda *a: calls.append(1) or real(*a))
    assert paper_privacy.why(six, *two, money=False) == ""
    assert len(calls) == 48
    del calls[:]
    assert paper_privacy.why(six_debt, *two, money=False) == "word debt"
    assert 0 < len(calls) <= 48
    del calls[:]
    assert paper_privacy.why(seven, seven, money=False) == "text too long to check"
    assert paper_privacy.why(six, six, *two, money=False) == "text too long to check"
    assert paper_privacy.why(six, six_debt, *two, money=False) == "text too long to check"
    assert calls == []
    monkeypatch.setattr(paper_privacy, "MAX_COMBOS", 49)
    assert paper_privacy.why(seven, seven, money=False) == ""
    assert len(calls) == 49
    print("PASS: exactly 48 combinations are checked and 49 or more are too long to check")


def test_the_work_of_every_combination_is_charged_to_the_work_budget(monkeypatch):
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    cases = (
        ["=?ut-8?B??=\r\n =?unknown?Q?=CE=94?= ", "=?utf-16?B?//6pAw?==?=", "\ufffd\ufffd a\u03bc\u03a9\n\u200b\x00\x00",
         "=?lati-1?Q??= \t=?utf-8?B?Cg==?= \t=?unknown?B??=\t=?utf-16?B?//4=?=", "\u03a9 \u00df??", " ", "\u03a9\ufffdb", "\n", "\n", "\n"],
        ["=?tf-8?B??=\rx\n=?n\ufffdknown?Q?\x00=CE=94?= x", "=?tf-8?B??=\rx\n=?n\ufffdknown?Q?\x00=CE=94?= x",
         "\u00e9\u03a9=?lat\u200bi1?Q??=\u0301\u00df\ufffd\x00\ufffd \u03a9\x00\nb\\\u0301\u0301", "\u0301?\u0301\u0301\ufffd \u00df\u0301\n\u200b?",
         "x?a?\x00\u200b\x00a", "? \u0301a\x00\u0301", "=?latin-1?Q?=20?=", "", "?", "\x00"],
    )
    done = [0]
    real = paper_privacy._plain_char
    monkeypatch.setattr(paper_privacy, "_plain_char", lambda c, gap: done.__setitem__(0, done[0] + 1) or real(c, gap))
    for texts in cases:
        done[0] = 0
        assert paper_privacy.why(*texts) in ("", "text too long to check")
        assert done[0] <= 4 * paper_privacy.WORK_BUDGET, done[0]
    del done[:]
    done.append(0)
    assert paper_privacy.why("Lunch at noon", sender="Bob Smith") == "" and paper_privacy._spent.n == 68
    assert paper_privacy.why("hi") == "" and paper_privacy._spent.n == 3 * paper_privacy.MIN_CHARGE == 66
    assert paper_privacy.why("a\u00df") == "" and paper_privacy._spent.n == 158
    assert paper_privacy.why("a\u00df\u03bc") == "" and paper_privacy._spent.n == 434
    assert paper_privacy.why("a" * 47 + "\u00df") == "" and paper_privacy._spent.n == 441
    plain_calls, mixed_calls = [], []
    real_plain, real_mixed = paper_privacy._plain_raw, paper_privacy._mixed_raw
    monkeypatch.setattr(paper_privacy, "_plain_raw", lambda text, gap=None: plain_calls.append((text, gap)) or real_plain(text, gap))
    monkeypatch.setattr(paper_privacy, "_mixed_raw", lambda text: mixed_calls.append(text) or real_mixed(text))
    assert paper_privacy.why("Lunch\tat noon", "second field here", money=False) == ""
    assert [c for c in plain_calls if c[0] == "second field here"] == [("second field here", None), ("second field here", ""), ("second field here", " ")]
    assert mixed_calls.count("second field here") == 1 and mixed_calls.count("Lunch\tat noon") == 1 and mixed_calls.count("Lunch at noon") == 1
    print("PASS: the work of all combinations is charged, and bounded by the work budget")


def test_the_reading_a_mail_client_shows_is_checked_beside_plain_non_ascii_text(monkeypatch):
    import email.policy
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    cases = (
        ("\u00e9  =?cp1252?B?Y2E=?=  =?cp1252?B?cmRp?=  =?us-ascii?Q?O=6Co=67i=73=74?=\r\n =?iso-8859-1?q?Ssi?=", "word cardiologist"),
        ("=?ISO-8859-1*de?b?cmVm?=  =?cp1252?q?=69l?=\n=?x-foo?Q?=C3=A9l?=\u0394", "word with mixed alphabets"),
    )
    for field, reason in cases:
        shown = str(email.message_from_string("Subject: " + field.replace("\n", "\r\n ") + "\n\nx", policy=email.policy.default)["Subject"])
        assert shown in paper_privacy._readings(field)
        assert paper_privacy.why(shown) == reason
        assert paper_privacy.why(field) == reason
        assert paper_privacy.why("Hello", sender=field + " <a@x.org>") == reason
    assert paper_privacy.why("caf\u00e9 =?utf-8?Q?lunch?=") == ""
    print("PASS: the mail client reading is checked beside plain non-ASCII text")


def test_a_charset_with_a_star_is_also_read_whole(monkeypatch):
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    for text in ("=?*utf-7?Q?+AGQAZQBiAHQ-?=", "=?utf*7?Q?+AGQAZQBiAHQ-?=", "=?utf-16*be?Q?=00d=00e=00b=00t?=", "x =?utf-16*be?Q?=00d=00e=00b=00t?= y", "=?utf-16*be?Q?=00d=00e?==?utf-16*be?Q?=00b=00t?="):
        assert paper_privacy.why(text, money=False), text
        assert paper_privacy.why("lunch", text, money=False), text
    assert paper_privacy.why("x", sender="=?*utf-7?Q?+AGQAZQBiAHQ-?= <a@ok.example>", money=False)
    assert not paper_privacy.why("=?utf-16*be?Q?=00L=00u=00n=00c=00h?=", money=False)
    assert paper_privacy._client("=?*utf-7?Q?+AGQAZQBiAHQ-?=", False) == "debt"
    monkeypatch.setattr(paper_privacy, "_client", lambda text, strip=True: "client %s" % strip)
    assert {"client True", "client False"} <= set(paper_privacy._readings("=?utf-16*be?Q?=00d?="))
    assert "client False" not in paper_privacy._readings("=?utf-16?Q?=00d?=")
    monkeypatch.setattr(paper_privacy, "_client", lambda text, strip=True: None)
    monkeypatch.setattr(paper_privacy, "_policy", lambda text: None)
    for text in ("=?*utf-7?Q?+AGQAZQBiAHQ-?=", "=?utf-16*be?Q?=00d=00e=00b=00t?=", "x=?utf-16*be?Q?=00d=00e=00b=00t?=y"):
        assert paper_privacy.why(text, money=False), text
    print("PASS: a charset with a star is read both without the language tag and as the whole label")


def test_a_charset_with_a_language_tag_is_unknown_when_tags_are_not_stripped():
    import paper_privacy
    assert paper_privacy._known("utf-7*en", True) and not paper_privacy._known("utf-7*en", False)
    assert paper_privacy._known("utf-7", False) and not paper_privacy._known("x-foo*en", True)
    print("PASS: a language-tagged charset is a known codec only when its tag is stripped")


def test_a_spaced_word_keeps_the_tag_unstripped_when_asked_not_to_strip():
    import paper_privacy
    field = "=?utf-7*en?Q?+AGQAZQBiAHQ-?="
    m = paper_privacy.ENCODED_RE.search(field)
    assert paper_privacy._spaced(m, None, True) == "debt"
    assert paper_privacy._spaced(m, None, False) == "+AGQAZQBiAHQ-"
    wrapped = "x" + field + "y"
    m = paper_privacy.ENCODED_RE.search(wrapped)
    assert paper_privacy._spaced(m, None, True) == " debt "
    assert paper_privacy._spaced(m, None, False) == " +AGQAZQBiAHQ- "
    print("PASS: the spaced reading of an encoded word honours the strip flag")


def test_the_memo_is_gone_after_every_check_however_it_ends(monkeypatch):
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    paper_privacy.why("Lunch at noon")
    assert getattr(paper_privacy._spent, "memo", "gone") is None
    paper_privacy.why("this is a debt")
    assert getattr(paper_privacy._spent, "memo", "gone") is None
    assert paper_privacy.why("x" * (paper_privacy.TOO_LONG + 1)) == "text too long to check"
    assert getattr(paper_privacy._spent, "memo", "gone") is None
    monkeypatch.setattr(paper_privacy, "_why_all", lambda *a: 1 / 0)
    assert paper_privacy.why("Lunch") == "text that could not be checked"
    assert getattr(paper_privacy._spent, "memo", "gone") is None
    print("PASS: the memo of one check never outlives it")


def test_a_words_result_is_remembered_per_money_setting():
    import paper_privacy
    paper_privacy._spent.n = 0
    paper_privacy._spent.memo = {}
    try:
        assert paper_privacy._words(("cost $5",), "", True) == "an amount of money"
        assert paper_privacy._words(("cost $5",), "", False) == ""
        assert paper_privacy._words(("cost $5",), "", True) == "an amount of money"
    finally:
        paper_privacy._spent.memo = None
    print("PASS: the remembered words result is kept apart for money on and money off")


def test_each_expanded_combination_is_charged_its_size_over_the_expand_weight_plus_the_base(monkeypatch):
    import paper_privacy
    units = []
    monkeypatch.setattr(paper_privacy, "_check", lambda *a: "")
    monkeypatch.setattr(paper_privacy, "_charge", units.append)
    assert paper_privacy.EXPAND_WEIGHT == 32 and paper_privacy.EXPAND_BASE == 1
    w = paper_privacy.EXPAND_WEIGHT
    count = len(paper_privacy.READINGS["ß"])
    for size, extra in ((w - 1, 0), (w, 1), (2 * w - 1, 1), (2 * w, 2)):
        text = "ß" + "a" * (size - 2)
        del units[:]
        assert paper_privacy._why((text,), "", (), False) == ""
        assert units == [paper_privacy.EXPAND_BASE + extra] * count, (size, units)
    print("PASS: a combination costs its size divided by the expand weight, rounded down, plus the base")


def test_the_readings_budget_is_exact(monkeypatch):
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    prod = len(paper_privacy.READINGS["ß"]) ** 2 * len(paper_privacy.READINGS["Δ"])
    assert paper_privacy.READINGS_BUDGET % prod == 0
    size = paper_privacy.READINGS_BUDGET // prod
    base = "ßß Δ "
    at = base + "a" * (size - 1 - len(base))
    over = at + "a"
    assert len(at) + 1 == size and prod * size == paper_privacy.READINGS_BUDGET
    monkeypatch.setattr(paper_privacy, "WORK_BUDGET", 10 ** 9)
    assert paper_privacy.why(at, money=False) == ""
    assert paper_privacy.why(over, money=False) == "word with mixed alphabets"
    print("PASS: a product of readings and size equal to the budget is expanded, one more is mixed alphabets")


def test_the_sender_is_charged_with_the_text_to_the_work_budget(monkeypatch):
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    assert paper_privacy.why("a" * 30, sender="b" * 10, money=False) == "" and paper_privacy._spent.n == 128
    assert paper_privacy.why("a" * 10, sender="b" * 30, money=False) == "" and paper_privacy._spent.n == 128
    assert paper_privacy.why("a" * 5, sender="b" * 5, money=False) == "" and paper_privacy._spent.n == 66
    print("PASS: the work charged is the text plus the sender, never less than the minimum")


def test_a_joined_reading_that_only_forms_an_encoded_start_across_two_readings_cannot_be_read(monkeypatch):
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    text = "?B?=?utf-8?Q?=3D=3Fz?="
    with pytest.raises(ValueError):
        paper_privacy._readings(text)
    assert paper_privacy.why(text, money=False) == "text that could not be checked"
    assert paper_privacy.why("?B?=?utf-8?Q?=3D=3Fz?= lunch", money=False) == "text that could not be checked"
    print("PASS: an encoded start formed across two readings is refused")


def test_a_text_is_searched_for_a_doctor_once_however_many_readings_repeat_it(monkeypatch):
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    calls = []
    real = paper_privacy.DR_RE
    class Spy:
        def search(self, s):
            calls.append(s)
            return real.search(s)
    monkeypatch.setattr(paper_privacy, "DR_RE", Spy())
    assert paper_privacy.why("hello", "a​b", money=False) == ""
    assert calls.count("hello") == 1, calls
    assert len(calls) == len(set(calls)), calls
    print("PASS: the doctor search runs once per distinct text within one check")


def test_the_work_and_memo_of_one_thread_are_not_seen_by_another(monkeypatch):
    import threading
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    assert paper_privacy.why("hi") == "" and paper_privacy._spent.n == 3 * paper_privacy.MIN_CHARGE
    out = {}

    def other():
        out["before"] = getattr(paper_privacy._spent, "n", "none")
        out["result"] = paper_privacy.why("a" * 100)
        out["after"] = paper_privacy._spent.n

    t = threading.Thread(target=other)
    t.start()
    t.join()
    assert out["before"] == "none"
    assert out["result"] == "" and out["after"] == 359
    assert paper_privacy._spent.n == 3 * paper_privacy.MIN_CHARGE
    print("PASS: each thread keeps its own work count and memo")


def _front_world(monkeypatch, events=(), today_tasks=(), overdue_tasks=(), upcoming_tasks=(), mails=()):
    import mailwatch
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    monkeypatch.setattr(paper, "_BIZ", None)
    monkeypatch.setattr(gcal, "list_events", lambda *a, **k: list(events))
    monkeypatch.setattr(tasks, "list_for", lambda day=None: list(today_tasks))
    monkeypatch.setattr(tasks, "overdue", lambda: list(overdue_tasks))
    monkeypatch.setattr(tasks, "upcoming", lambda limit=40: list(upcoming_tasks))
    monkeypatch.setattr(mailwatch, "digest", lambda hours=24: {"total": len(mails), "needs_you": list(mails), "suspicious": 0})
    monkeypatch.setattr(gmail, "accounts", lambda: [{"name": "main", "email": "owner@example.com"}] if mails else [])


def _front_strings(fr):
    out = [fr["headline"]]
    for a in fr["agenda"]:
        out += [a["time"], a["what"], a["note"], a["tag"]]
    out += [w["text"] for w in fr["week"]] + [x["text"] for x in fr["due"]]
    for x in fr["look"]:
        out += [x["who"], x["text"]]
    return [s for s in out if s]


def _front_for(monkeypatch, deliv=None, **world):
    import paper_privacy
    _front_world(monkeypatch, **world)
    fr = paper.front(paper.settings(), NOW.date(), deliv or {"today": [], "soon": [], "delivered": []}, paper_fake.weather)
    for s in _front_strings(fr):
        assert not paper_privacy.private(s), s
    return fr


def _ev(title, when, location="", all_day=False, eid="z"):
    return {"id": eid, "title": title, "start": when.isoformat(), "end": when.isoformat(), "all_day": all_day, "location": location}


def _gone(fr, word):
    assert word not in json.dumps(fr).lower(), word


def test_a_glued_clock_time_cannot_hide_a_word_from_the_check_on_a_today_event(monkeypatch):
    nine = NOW.replace(hour=9, minute=0)
    fr = _front_for(monkeypatch, events=[_ev("9:00chase", nine), _ev("9:00 Standup", nine.replace(hour=10), eid="y")])
    _gone(fr, "chase")
    assert any(a["what"] == "9:00 Standup" or a["what"] == "Standup" for a in fr["agenda"])
    print("PASS: a today event whose glued clock time hid a word is left out of the agenda and the headline")


def test_a_glued_clock_time_cannot_hide_a_word_on_a_later_day_or_an_all_day_event(monkeypatch):
    nine = (NOW + dt.timedelta(days=1)).replace(hour=9, minute=0)
    fr = _front_for(monkeypatch, events=[_ev("9:00chase", nine), _ev("9:00 Standup", nine.replace(hour=10), eid="y")])
    _gone(fr, "chase")
    assert fr["week"] and "Standup" in fr["week"][0]["text"]
    print("PASS: a later-day event whose glued clock time hid a word is left out of the week")


def test_a_location_cut_at_eighty_characters_cannot_end_on_a_word_the_whole_did_not(monkeypatch):
    import paper_privacy
    place = "q" * 75 + " bankers"
    assert not paper_privacy.private("Lunch", place) and paper_privacy.private(place[:80])
    fr = _front_for(monkeypatch, events=[_ev("Lunch", NOW.replace(hour=12), place), _ev("Walk", NOW.replace(hour=14), eid="y")])
    _gone(fr, "bank")
    assert any(a["what"] == "Walk" for a in fr["agenda"])
    print("PASS: an event whose place is cut into a hidden word is left out")


def test_a_headline_cut_cannot_end_on_a_word_the_whole_title_did_not(monkeypatch):
    import paper_privacy
    title = "q" * 27 + "-bankers" + "y" * 20
    assert not paper_privacy.private(title) and paper_privacy.private(title[:32])
    fr = _front_for(monkeypatch, events=[_ev(title, NOW.replace(hour=12)), _ev("Walk", NOW.replace(hour=14), eid="y")])
    _gone(fr["headline"], "bank")
    assert "Walk" in fr["headline"]
    print("PASS: a title cut for the headline cannot end on a hidden word")


def test_joined_week_titles_cannot_form_a_security_code_that_each_alone_did_not(monkeypatch):
    import paper_privacy
    tomorrow = (NOW + dt.timedelta(days=1)).replace(hour=0, minute=0)
    one, two = "my code", "123456 notes"
    assert not paper_privacy.private(one) and not paper_privacy.private(two) and paper_privacy.private(f"{one}; {two}.")
    fr = _front_for(monkeypatch, events=[_ev(one, tomorrow, all_day=True), _ev(two, tomorrow, all_day=True, eid="y")])
    assert fr["week"] and "my code" in fr["week"][0]["text"] and "123456" not in fr["week"][0]["text"]
    print("PASS: week titles are joined only while the joined line stays printable")


def test_joined_headline_titles_cannot_form_a_security_code_that_each_alone_did_not(monkeypatch):
    import paper_privacy
    first, second = "1234 notes", "x code"
    fr = _front_for(monkeypatch, events=[_ev(first, NOW.replace(hour=9)), _ev(second, NOW.replace(hour=10), eid="y")])
    assert first in fr["headline"] and second not in fr["headline"] and "1 more on the calendar" in fr["headline"]
    assert any(a["what"] == second for a in fr["agenda"])
    assert not paper_privacy.private(first) and not paper_privacy.private(second)
    print("PASS: a headline title that would join into a code is counted as one more instead of printed")


def test_an_overdue_task_line_with_its_date_cannot_form_a_code_the_title_did_not(monkeypatch):
    import paper_privacy
    late = [{"id": 1, "title": "Write down the new pin", "due": "2026-11-01", "done": 0}, {"id": 2, "title": "Buy milk", "due": "2026-11-02", "done": 0}]
    assert not paper_privacy.private(late[0]["title"]) and paper_privacy.private("Write down the new pin (was due 2026-11-01)")
    fr = _front_for(monkeypatch, overdue_tasks=late)
    assert [x["text"] for x in fr["due"]] == ["Buy milk (was due 2026-11-02)"]
    assert "1 overdue task" in fr["dek"]
    print("PASS: an overdue task whose printed line holds a code is left out")


def test_a_mail_summary_cut_at_160_or_subject_cut_at_120_cannot_end_on_a_hidden_word(monkeypatch):
    import paper_privacy
    sm = "q" * 155 + " bankers"
    sj = "q" * 115 + " bankers"
    base = {"sender": "Pat <pat@example.org>", "sender_name": "Pat", "snippet": ""}
    mails = [dict(base, subject="Hello", summary=sm), dict(base, subject=sj, summary=""), dict(base, subject="Lunch", summary="Noon works.")]
    assert not any(paper_privacy.private(m["sender_name"], m["subject"], m["summary"], m["snippet"], sender=m["sender"]) for m in mails)
    fr = _front_for(monkeypatch, mails=mails)
    _gone(fr, "bank")
    assert [x["text"] for x in fr["look"]] == ["Noon works."]
    print("PASS: a mail whose cut summary or subject ends on a hidden word is left out")


def test_a_task_title_printed_whole_is_checked_whole(monkeypatch):
    t = lambda i, s: {"id": i, "title": s, "due": NOW.date().isoformat(), "done": 0}
    fr = _front_for(monkeypatch, today_tasks=[t(1, "Pay the bill"), t(2, "Water plants")])
    assert [x["what"] for x in fr["agenda"] if x["tag"] == "DUE"] == ["Water plants"]
    print("PASS: a task title is printed only when it was checked as printed")


def test_a_delivery_line_is_checked_as_printed(monkeypatch):
    rows = {"today": [{"store": "Shop", "what": "Lamp", "status": "out for delivery", "carrier": "VA", "sent": NOW.date().isoformat(), "window": "9-11 am"},
                      {"store": "Shop", "what": "Chair", "status": "out for delivery", "carrier": "UPS", "sent": NOW.date().isoformat(), "window": ""}],
            "soon": [], "delivered": []}
    kept = paper.private_out(rows)
    assert [r["what"] for r in kept["today"]] == ["Chair"]
    fr = _front_for(monkeypatch, deliv=rows)
    _gone(fr, "lamp")
    assert any("Chair" in a["what"] for a in fr["agenda"])
    print("PASS: a delivery whose printed line holds a hidden word is left out")


def _no_words_charge(monkeypatch, paper_privacy):
    monkeypatch.setattr(paper_privacy, "_words", lambda texts, sender, money: "")


def test_every_first_pass_over_the_text_is_charged_to_the_work_budget(monkeypatch):
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    _no_words_charge(monkeypatch, paper_privacy)
    assert paper_privacy.NON_ASCII_WEIGHT == 2
    for texts, sender in ((("hi",), ""), (("a" * 500,), ""), (("a" * 30, "b" * 7), "c" * 11), (("日" * 3000,), "")):
        assert paper_privacy.why(*texts, sender=sender, money=False) == ""
        blob = " ".join(texts) + " " + sender
        assert paper_privacy._spent.n == 2 * max(paper_privacy.MIN_CHARGE, len(blob) + sum(c > "\x7f" for c in blob)), (texts, paper_privacy._spent.n)
    print("PASS: the two first passes of a check are charged their text length, never less than the minimum")


def test_the_first_pass_of_every_combination_is_charged_too(monkeypatch):
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    _no_words_charge(monkeypatch, paper_privacy)
    seen = []
    real = paper_privacy._first
    monkeypatch.setattr(paper_privacy, "_first", lambda texts, sender, hidden, money: seen.append(len(" ".join(texts) + " " + sender) + sum(c > "\x7f" for c in " ".join(texts) + " " + sender)) or real(texts, sender, hidden, money))
    a, b = "=?utf-8?B?YQ==?=", "=?utf-8?Q?b?="
    assert paper_privacy.why(a + " x", b + " y", sender="s", money=False) == ""
    assert len(seen) >= 2 + 2 * 2
    assert paper_privacy._spent.n == sum(max(paper_privacy.MIN_CHARGE, n) for n in seen), (seen, paper_privacy._spent.n)
    print("PASS: each reading combination pays for its own first passes")


def test_the_characters_searched_for_a_word_never_exceed_the_work_budget(monkeypatch):
    import paper_privacy
    monkeypatch.setattr(paper_privacy, "hidden_senders", lambda: ())
    real = paper_privacy._WORD_RE
    scanned = []

    class Spy:
        def search(self, s, *a):
            scanned.append(len(s))
            return real.search(s, *a)

    monkeypatch.setattr(paper_privacy, "_WORD_RE", Spy())
    def ew(s, cs="utf-8"):
        return "=?%s?B?%s?=" % (cs, base64.b64encode(s.encode(cs.split("*")[0])).decode())
    encoded = " ".join(ew(w, cs) for w, cs in (("a", "utf-8"), ("ß", "utf-8*en"), ("x y", "utf-16"), ("d e", "iso-8859-1")))
    cases = (["日" * 3500, encoded], [encoded, "ก" * 3400, encoded], ["a." * 1700, encoded], ["1234 " * 700, encoded],
             ["ßß�" + "1234 " * 700, encoded],
             ["lunch plan " * 40 + "=?us-ascii?B?Pw==?=\u03941234 " + "lunch plan " * 300, "=?iso-8859-1?B?Pw==?=\u03a9"])
    for texts in cases:
        del scanned[:]
        assert paper_privacy.why(*texts, money=True) in ("", "text too long to check", "word with mixed alphabets"), [len(t) for t in texts]
        assert sum(scanned) <= paper_privacy.WORK_BUDGET + max(scanned), (sum(scanned), max(scanned), [len(t) for t in texts])
    print("PASS: the characters run through the word search stay within the work budget")


def test_making_a_text_plain_is_charged_once_per_distinct_text_and_gap_and_non_ascii_costs_double():
    import paper_privacy
    assert paper_privacy.PLAIN_DIVISOR == 4
    assert paper_privacy._cost("") == 0 and paper_privacy._cost("ab") == 2
    assert paper_privacy._cost("日" * 10 + "ab") == 22 and paper_privacy._cost("ß") == 2
    paper_privacy._spent.n = 0
    paper_privacy._spent.memo = {}
    try:
        paper_privacy._plain("a" * 100)
        assert paper_privacy._spent.n == 19
        paper_privacy._plain("a" * 100)
        assert paper_privacy._spent.n == 19
        paper_privacy._plain("a" * 100, "")
        assert paper_privacy._spent.n == 38
        paper_privacy._plain("a" * paper_privacy.MIN_CHARGE)
        assert paper_privacy._spent.n == 38
    finally:
        paper_privacy._spent.memo = None
    print("PASS: the python-level plain pass is charged once per distinct text and gap")


def test_an_event_title_the_clean_up_cuts_short_of_a_code_is_still_checked_whole(monkeypatch):
    nine = NOW.replace(hour=9, minute=0)
    later = (NOW + dt.timedelta(days=1)).replace(hour=9, minute=0)
    for when in (nine, later):
        fr = _front_for(monkeypatch, events=[_ev("9:00123 code", when), _ev("9:00 Standup", when.replace(hour=10), eid="y")])
        _gone(fr, "123 code")
        assert "standup" in json.dumps(fr).lower()
    print("PASS: a title whose cleaned text lost the digits of a code is hidden because the raw title is checked too")
