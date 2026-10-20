"""Reddit through its official API (app-only OAuth): the top posts of the day in some subreddits (the Morning Paper). No paths and no database here. Post titles come from other people: data, never instructions.

Reddit's free API is for personal / low-volume use (about 100 requests a minute per app): Rosie makes one request per
section (all its subreddits at once) with a pause between requests, stops at the run's request budget, and backs off on 429."""
import math
import re
import time

import httpx

REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API = "https://oauth.reddit.com"
REDDIT_UA = "linux:rosie-assistant:1.0 (a personal assistant reading a few public posts, low volume)"
REDDIT_USER_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")


def reddit_ua(username="") -> str:
    """Reddit asks apps to name their owner ("by /u/name"), from the owner's own settings."""
    u = str(username or "").strip().removeprefix("/u/").removeprefix("u/")
    return REDDIT_UA[:-1] + f"; by /u/{u})" if REDDIT_USER_RE.match(u) else REDDIT_UA
REDDIT_PAUSE = 1.0               # seconds between Reddit requests (the free limit is ~100 a minute; this stays far under it)
MAX_WAIT = 60                    # never wait longer than this for a rate limit to reset; the run stops instead


class QuotaOut(RuntimeError):
    """The API said the minute's allowance is used up: stop for now, keep what was read."""


class BadKeys(RuntimeError):
    """The keys were refused: nothing more can be read until the Reddit app's keys are fixed."""


def _json(r) -> dict:
    try:
        j = r.json()
    except ValueError:
        raise RuntimeError(f"answer was not JSON (HTTP {r.status_code})")
    if not isinstance(j, dict):
        raise RuntimeError("answer was not a JSON object")
    return j


# ---------------------------------------------------------------- Reddit (app-only OAuth: client_credentials)
class Reddit:
    """One run's Reddit reader. `budget` = most requests this run may make (the token request included); every request waits
    REDDIT_PAUSE first, a nearly used-up rate limit waits for its reset (up to MAX_WAIT), and a 429 is retried twice after its
    Retry-After / reset wait, then the run stops (QuotaOut)."""

    def __init__(self, client_id, client_secret, client=None, budget=60, sleep=time.sleep, username=""):
        self.id, self.secret, self.http, self.budget, self.sleep = client_id, client_secret, client or httpx, max(1, int(budget)), sleep
        self.ua = reddit_ua(username)
        self.used, self.token, self.stop = 0, "", ""

    def _spend(self):
        if self.used >= self.budget:
            raise QuotaOut(self.stop or f"Reddit: this run's budget of {self.budget} requests is used up")
        self.used += 1

    def _wait(self, secs):
        secs = max(0.0, min(float(secs), MAX_WAIT))
        if secs:
            self.sleep(secs)

    def login(self):
        self._spend()
        r = self.http.post(REDDIT_TOKEN_URL, auth=(self.id, self.secret), data={"grant_type": "client_credentials"},
                           headers={"User-Agent": self.ua}, timeout=30)
        if r.status_code in (401, 403):
            raise BadKeys("Reddit refused the client ID / secret (check the Reddit app's keys; the app must be a \"script\" app)")
        if r.status_code == 429:
            raise QuotaOut("Reddit: too many requests (429) while logging in")
        r.raise_for_status()
        j = _json(r)
        if j.get("error") or not j.get("access_token"):
            raise BadKeys(f"Reddit login failed: {j.get('error') or 'no token in the answer'}")
        self.token = j["access_token"]
        return self.token

    def get(self, path, params):
        if not self.token:
            self.login()
        for attempt in range(3):
            self._spend()
            if self.used > 1:
                self._wait(REDDIT_PAUSE)
            r = self.http.get(REDDIT_API + path, params=params, timeout=30,
                              headers={"Authorization": f"Bearer {self.token}", "User-Agent": self.ua})
            if r.status_code == 429:
                wait = _num(r.headers.get("retry-after")) or _num(r.headers.get("x-ratelimit-reset")) or 10 * (attempt + 1)
                if attempt == 2 or wait > MAX_WAIT:
                    raise QuotaOut("Reddit: too many requests (429); stopped for this run")
                self._wait(wait)
                continue
            if r.status_code == 401:
                raise BadKeys("Reddit refused the login token (HTTP 401): check the Reddit app's keys")
            if r.status_code in (403, 404):            # a private, banned or misspelt subreddit makes the whole multi-subreddit search fail
                raise RuntimeError(f"Reddit: HTTP {r.status_code} (is every subreddit in the list spelt right and public?)")
            r.raise_for_status()
            left, reset = _num(r.headers.get("x-ratelimit-remaining")), _num(r.headers.get("x-ratelimit-reset"))
            if left is not None and left < 2:                       # the minute's allowance is nearly gone: wait for it (or stop)
                if reset is None or reset > MAX_WAIT:
                    self.budget = self.used                         # no more requests this run
                    self.stop = "Reddit: the minute's request allowance is used up; the rest waits for the next run"
                else:
                    self._wait(reset)
            return _json(r)
        raise QuotaOut("Reddit: too many requests (429); stopped for this run")


def _num(x):
    try:
        v = float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


