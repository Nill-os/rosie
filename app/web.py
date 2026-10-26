import html
import re
import time
import urllib.parse

import httpx

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) personal-assistant"}


def _text(h: str) -> str:
    h = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h)).strip()


def _private_host(url: str) -> bool:
    import ipaddress
    import socket
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
    except (socket.gaierror, ValueError):
        return False
    return False


def fetch_url(url: str, max_chars: int = 3500):
    if _private_host(url):
        return {"error": "That address is on this computer or the local network, so I will not open it."}
    r = scrape(url)
    if "error" in r:
        return {"error": r["error"]}
    return {"url": r["url"], "status": r["status"], "text": _text(r["html"])[:min(int(max_chars), 6000)]}


# board #800 owner-scrapling-for-all-agents
_scrapling_get = None


def _fetcher():
    if _scrapling_get is not None:
        return _scrapling_get
    import logging
    from scrapling.fetchers import Fetcher
    logging.getLogger("scrapling").setLevel(logging.WARNING)
    return Fetcher.get


def scrape(url: str, timeout: float = 20, max_hops: int = 5) -> dict:
    # board #800 safety-redirect-hop-check
    cur = str(url)
    for _ in range(max_hops + 1):
        prob = public_url_problem(cur)
        if prob:
            return {"error": prob, "url": cur}
        try:
            r = _fetcher()(cur, follow_redirects=False, timeout=timeout, retries=1)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
        loc = {str(k).lower(): v for k, v in dict(r.headers or {}).items()}.get("location")
        if r.status in (301, 302, 303, 307, 308) and loc:
            cur = urllib.parse.urljoin(cur, loc)
            continue
        body = r.body if isinstance(r.body, bytes) else str(r.body or "").encode()
        return {"url": cur, "status": r.status, "html": body.decode(r.encoding or "utf-8", "replace")}
    return {"error": "That page sent me through too many redirects.", "url": cur}


PUBLIC_PORTS = (80, 443)
_public_transport = None


def _resolve(host: str) -> list:
    import socket
    try:
        return [info[4][0] for info in socket.getaddrinfo(host, None)]
    except (socket.gaierror, UnicodeError, ValueError, OSError):
        return []


def _check_public(url: str) -> tuple[str | None, list]:
    import ipaddress
    try:
        p = urllib.parse.urlsplit(str(url))
        port = p.port
    except ValueError:
        return "That is not a valid web address.", []
    if p.scheme not in ("http", "https"):
        return "Only web addresses (http or https) are opened.", []
    if p.username or p.password:
        return "Addresses with a login in them are not opened.", []
    host = (p.hostname or "").strip(".")
    if not host:
        return "That web address has no site name.", []
    if port is not None and port not in PUBLIC_PORTS:
        return "Only the normal web ports are used.", []
    ips = _resolve(host)
    if not ips:
        return "That site name could not be looked up.", []
    for raw in ips:
        try:
            ip = ipaddress.ip_address(str(raw).split("%")[0])
        except ValueError:
            return "That site name could not be looked up.", []
        mapped = getattr(ip, "ipv4_mapped", None)
        if not ip.is_global or ip.is_multicast or (mapped is not None and (not mapped.is_global or mapped.is_multicast)):
            return "That address is on this computer or the local network, so it is not opened.", []
    return None, [str(x).split("%")[0] for x in ips]


def public_url_problem(url: str) -> str | None:
    return _check_public(url)[0]


def _pinned(url: str, ip: str) -> tuple[str, str, str]:
    p = urllib.parse.urlsplit(url)
    lit = f"[{ip}]" if ":" in ip else ip
    netloc = lit + (f":{p.port}" if p.port is not None else "")
    host = p.hostname + (f":{p.port}" if p.port is not None else "")
    return urllib.parse.urlunsplit((p.scheme, netloc, p.path or "/", p.query, "")), host, p.hostname


_browser_session = None


class _BrowserHop:
    # board #800 safety-no-redirects-no-cookies

    def __init__(self, url: str, ip: str, timeout: float):
        from curl_cffi import CurlOpt
        from curl_cffi.requests import Session
        p = urllib.parse.urlsplit(url)
        pin = f"{p.hostname}:{p.port or (443 if p.scheme == 'https' else 80)}:{f'[{ip}]' if ':' in ip else ip}"
        self.s = (_browser_session or Session)(impersonate="chrome", curl_options={CurlOpt.RESOLVE: [pin]}, trust_env=False,
                                               allow_redirects=False, discard_cookies=True)
        self.url, self.timeout, self.r = url, timeout, None

    def __enter__(self):
        self.r = self.s.get(self.url, stream=True, timeout=self.timeout)
        self.status_code, self.headers = self.r.status_code, self.r.headers
        m = re.search(r"charset=([\w.-]+)", self.headers.get("content-type") or "", re.I)
        self.encoding = m.group(1) if m else None
        return self

    def iter_bytes(self):
        return self.r.iter_content()

    def __exit__(self, *exc):
        if self.r is not None:
            self.r.close()
        self.s.close()


def get_public(url: str, *, max_bytes: int, accept: tuple = ("text/html", "application/xhtml+xml"), timeout: float = 15,
               headers: dict | None = None, stop=None, max_hops: int = 5, check=None, browser: bool = False) -> dict:
    import http.cookiejar
    cur = str(url)
    deadline = time.monotonic() + max(timeout * 4, 30)  # board #800 why-read-deadline
    with httpx.Client(transport=_public_transport, timeout=timeout, follow_redirects=False, headers=headers or UA, trust_env=False) as c:
        c.cookies.jar.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
        for _ in range(max_hops + 1):
            prob, ips = _check_public(cur)
            prob = prob or (check(cur) if check else None)
            if prob:
                return {"error": prob, "url": cur, "blocked": True}
            target, host, sni = _pinned(cur, ips[0])
            c.cookies.clear()
            real_browser = browser and _public_transport is None
            try:
                with (_BrowserHop(cur, ips[0], timeout) if real_browser else
                      c.stream("GET", target, headers={"Host": host}, extensions={"sni_hostname": sni})) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        cur = urllib.parse.urljoin(cur, r.headers["location"])
                        continue
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    if accept and not any(ctype.startswith(a) for a in accept):
                        return {"error": f"Not a readable page ({ctype or 'unknown type'}).", "url": cur, "status": r.status_code, "content_type": ctype}
                    try:
                        if int(r.headers.get("content-length") or 0) > max_bytes:
                            return {"error": "Too big.", "url": cur, "status": r.status_code, "content_type": ctype}
                    except ValueError:
                        pass
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        if stop is not None and stop.is_set():
                            return {"error": "Stopped.", "url": cur, "stopped": True}
                        if time.monotonic() > deadline:
                            return {"error": "Too slow (the page took over a minute).", "url": cur, "status": r.status_code}
                        buf += chunk
                        if len(buf) > max_bytes:
                            return {"error": "Too big.", "url": cur, "status": r.status_code, "content_type": ctype}
                    return {"url": cur, "status": r.status_code, "content_type": ctype, "body": bytes(buf), "encoding": r.encoding or "utf-8"}
            except httpx.HTTPError as e:
                return {"error": f"{type(e).__name__}: {e}"[:200], "url": cur}
            except Exception as e:
                if not real_browser:
                    raise
                return {"error": f"{type(e).__name__}: {e}"[:200], "url": cur}
        return {"error": "Too many redirects.", "url": cur}


def web_search(query: str):
    try:
        r = httpx.post("https://html.duckduckgo.com/html/", data={"q": query}, headers=UA, timeout=20)
        out = []
        for m in re.finditer(r'(?s)class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', r.text):
            u = m.group(1)
            q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query).get("uddg")
            out.append({"title": _text(m.group(2)), "url": q[0] if q else u, "snippet": _text(m.group(3))})
            if len(out) >= 5:
                break
        return out or {"error": "No results (search may be blocked or down)."}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "rain showers", 82: "violent showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}


def weather(place: str = "", days: int = 3):
    import config
    import requests
    cfg = config.load()
    place = (place or (cfg.get("paper") or {}).get("place") or "").strip()
    if not place:
        return {"error": "Which place? No home area is set (Settings > Newspaper > Place)."}
    try:
        g = requests.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": place, "count": 1}, timeout=10).json()
        if not g.get("results"):
            return {"error": f"I could not find a place called '{place}'."}
        loc = g["results"][0]
        w = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": loc["latitude"], "longitude": loc["longitude"], "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch", "timezone": "auto", "forecast_days": max(1, min(7, int(days))),
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum"}, timeout=10).json()
        c, d = w["current"], w["daily"]
        days = [{"date": d["time"][i], "summary": WMO.get(d["weather_code"][i], "mixed"), "high_f": d["temperature_2m_max"][i], "low_f": d["temperature_2m_min"][i],
                 "rain_chance_pct": d["precipitation_probability_max"][i], "rain_in": d["precipitation_sum"][i]} for i in range(len(d["time"]))]
        return {"place": f"{loc['name']}, {loc.get('admin1', '')}", "now": {"temp_f": c["temperature_2m"], "feels_like_f": c["apparent_temperature"], "sky": WMO.get(c["weather_code"], "mixed"), "wind_mph": c["wind_speed_10m"]},
                "days": days, "source": "open-meteo.com"}
    except Exception as e:
        return {"error": f"Weather lookup failed: {type(e).__name__}: {e}"}


def _iter_ld(node):
    if isinstance(node, list):
        for x in node:
            yield from _iter_ld(x)
    elif isinstance(node, dict):
        yield node
        for v in node.values():
            if isinstance(v, (list, dict)):
                yield from _iter_ld(v)


def parse_recipe(page_html: str, url: str = "") -> dict | None:
    import json
    for m in re.finditer(r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page_html):
        try:
            data = json.loads(m.group(1).strip())
        except ValueError:
            continue
        for node in _iter_ld(data):
            t = node.get("@type")
            if not (t == "Recipe" or (isinstance(t, list) and "Recipe" in t)):
                continue
            ings = [html.unescape(re.sub(r"<[^>]+>", "", str(i))).strip() for i in (node.get("recipeIngredient") or [])]
            steps = []
            for s in node.get("recipeInstructions") or []:
                if isinstance(s, str):
                    steps.append(s)
                elif isinstance(s, dict):
                    if s.get("@type") == "HowToSection":
                        steps += [x.get("text", "") for x in s.get("itemListElement", []) if isinstance(x, dict)]
                    else:
                        steps.append(s.get("text", ""))
            steps = [html.unescape(re.sub(r"<[^>]+>", "", x)).strip() for x in steps if x and x.strip()]
            if len(ings) < 3 or len(steps) < 2:
                continue
            ag = node.get("aggregateRating") or {}
            try:
                rating, count = float(ag.get("ratingValue") or 0), int(float(ag.get("ratingCount") or ag.get("reviewCount") or 0))
            except (TypeError, ValueError):
                rating, count = 0.0, 0
            y = node.get("recipeYield")
            return {"name": html.unescape(str(node.get("name", ""))), "url": url, "rating": rating, "reviews": count, "ingredients": ings, "steps": steps,
                    "total_time": node.get("totalTime", ""), "yield": (y[0] if isinstance(y, list) and y else y) or ""}
    return None


def recipe_score(r: dict) -> float:
    import math
    return r["rating"] * math.log10(r["reviews"] + 10) if r["rating"] else 0.5


def find_recipe(query: str):
    from concurrent.futures import ThreadPoolExecutor
    res = web_search(query if "recipe" in query.lower() else query + " recipe")
    if isinstance(res, dict):
        return res
    skip = ("pinterest.", "youtube.", "facebook.", "instagram.", "tiktok.", "reddit.")
    urls = [x["url"] for x in res if not any(s in x["url"] for s in skip)][:6]

    def one(u):
        r = scrape(u, timeout=15)
        try:
            return parse_recipe(r["html"], r["url"]) if "html" in r else None
        except Exception:
            return None
    with ThreadPoolExecutor(4) as ex:
        found = [r for r in ex.map(one, urls) if r]
    if not found:
        return {"error": "I could not read real recipe data from the top results. Say so plainly; do not invent amounts. You can fetch_url one of: " + ", ".join(urls[:3])}
    found.sort(key=recipe_score, reverse=True)
    best = found[0]
    return {"best": best, "others": [{"name": r["name"], "url": r["url"], "rating": r["rating"], "reviews": r["reviews"]} for r in found[1:3]],
            "note": "Ingredients and steps are copied from the page's own recipe data. Put the source URL in anything you save, and say in one line why this one."}
