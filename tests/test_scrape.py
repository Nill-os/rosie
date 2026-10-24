import web


class R:
    def __init__(self, status=200, body=b"", headers=None):
        self.status, self.body, self.headers, self.encoding = status, body, headers or {}, "utf-8"


def fake(pages, calls):
    def get(u, **k):
        calls.append((u, k))
        return pages[u]
    return get


def test_fetch_url_reads_the_page_with_scrapling(monkeypatch):
    calls = []
    monkeypatch.setattr(web, "_private_host", lambda u: False)
    monkeypatch.setattr(web, "public_url_problem", lambda u: None)
    monkeypatch.setattr(web, "_scrapling_get", fake({"https://a.test/x": R(body=b"<html><script>no</script><p>Hello there</p></html>")}, calls))
    out = web.fetch_url("https://a.test/x")
    assert out == {"url": "https://a.test/x", "status": 200, "text": "Hello there"}
    assert calls[0][1]["follow_redirects"] is False                      # redirects are checked one hop at a time, not by Scrapling


def test_redirects_are_followed_and_every_hop_is_checked(monkeypatch):
    calls, checked = [], []
    pages = {"https://a.test/x": R(301, headers={"Location": "/y"}), "https://a.test/y": R(302, headers={"location": "https://b.test/z"}),
             "https://b.test/z": R(body=b"done")}
    monkeypatch.setattr(web, "public_url_problem", lambda u: checked.append(u))
    monkeypatch.setattr(web, "_scrapling_get", fake(pages, calls))
    out = web.scrape("https://a.test/x")
    assert out["url"] == "https://b.test/z" and out["html"] == "done"
    assert checked == ["https://a.test/x", "https://a.test/y", "https://b.test/z"]


def test_a_redirect_to_this_pc_or_the_lan_is_refused(monkeypatch):
    calls = []
    pages = {"https://a.test/x": R(302, headers={"Location": "http://192.168.1.8:8006/"})}
    monkeypatch.setattr(web, "public_url_problem", lambda u: "local network" if "192.168" in u else None)
    monkeypatch.setattr(web, "_scrapling_get", fake(pages, calls))
    out = web.scrape("https://a.test/x")
    assert out["error"] == "local network" and [u for u, _ in calls] == ["https://a.test/x"]   # the LAN address is never requested


def test_too_many_redirects_and_fetch_errors(monkeypatch):
    monkeypatch.setattr(web, "public_url_problem", lambda u: None)
    monkeypatch.setattr(web, "_scrapling_get", lambda u, **k: R(302, headers={"Location": u + "x"}))
    assert "too many redirects" in web.scrape("https://a.test/", max_hops=3)["error"]

    def boom(u, **k):
        raise TimeoutError("slow")
    monkeypatch.setattr(web, "_scrapling_get", boom)
    monkeypatch.setattr(web, "_private_host", lambda u: False)
    assert web.fetch_url("https://a.test/") == {"error": "TimeoutError: slow"}


def test_scrapling_is_installed_for_rosie():
    import pytest
    fetchers = pytest.importorskip("scrapling.fetchers")              # the public copy's CI installs only requirements-test.txt
    assert callable(fetchers.Fetcher.get)
