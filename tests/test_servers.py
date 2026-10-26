# board #893 owner-generic-servers-page
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import servers  # noqa: E402

PROXMOX = """MACHINE host Home running -
PORT host 10.77.4.8:22 users:(("sshd",pid=1,fd=6))
PORT host 127.0.0.1:85 users:(("pvedaemon
PORT host *:8006 users:(("pveproxy
PORT host *:3128 users:(("spiceproxy
PORT host 100.64.0.1%tailscale0:47574 users:(("tailscaled",pid=7,fd=19))
MACHINE 701 photos running 10.77.4.52
PORT 701 127.0.0.1:5432 users:(("postgres",pid=1,fd=7))
PORT 701 *:2283 users:(("immich-api",pid=2,fd=47))
PORT 701 *:38231 users:(("immich",pid=3,fd=94))
PORT 701 [::]:2283 users:(("immich-api",pid=2,fd=48))
MACHINE 702 dns running 10.77.4.53
PORT 702 0.0.0.0:53 users:(("pihole-FTL",pid=1,fd=24))
PORT 702 0.0.0.0:80 users:(("pihole-FTL",pid=1,fd=33))
PORT 702 0.0.0.0:443 users:(("pihole-FTL",pid=1,fd=38))
MACHINE 703 old stopped -
"""


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(servers, "STATE", tmp_path / "servers.json")
    monkeypatch.setattr(servers, "state", {"running": False, "last_error": ""})
    monkeypatch.setattr(servers.security_watch, "_ssh_cmd", lambda: ["ssh", "-i", "k", "root@10.77.4.8"])
    monkeypatch.setattr(servers.security_watch, "_run", lambda argv, timeout, stdin=None: PROXMOX)
    webs = {8006: {"scheme": "https", "title": "Home - Proxmox Virtual Environment"}, 2283: {"scheme": "http", "title": "Immich"},
            80: {"scheme": "", "title": ""}, 443: {"scheme": "", "title": ""}}  # board #893 quirk-pihole-busy-no-answer
    monkeypatch.setattr(servers, "probe", lambda ip, port: webs.get(port, {"scheme": "", "title": ""}))
    monkeypatch.setattr(servers, "_up", lambda ip, port: None if port == 22 else 4)
    return tmp_path


def by_name(d):
    return {m["name"]: m for m in d["machines"]}


def test_she_finds_the_server_its_containers_and_what_can_be_opened(home):
    m = by_name(servers.discover())
    assert m["Home"]["ip"] == "10.77.4.8" and m["Home"]["kind"] == "server"  # board #893 why-login-address-not-vpn
    assert [(s["port"], s["label"], s["url"] or s["hint"]) for s in m["Home"]["services"]] == [
        (22, "SSH", "ssh root@10.77.4.8"), (8006, "Proxmox", "https://10.77.4.8:8006/")]
    assert [(s["port"], s["label"], s["url"]) for s in m["photos"]["services"]] == [(2283, "Immich", "http://10.77.4.52:2283/")]
    assert [(s["port"], s["label"], s["url"]) for s in m["dns"]["services"]] == [(53, "DNS", ""), (443, "Pi-hole", "https://10.77.4.53/admin/")]
    assert m["old"]["running"] is False and m["old"]["services"] == [] and m["old"]["ip"] == ""


def test_a_plain_server_without_proxmox(home, monkeypatch):
    monkeypatch.setattr(servers.security_watch, "_run", lambda argv, timeout, stdin=None:
                        "MACHINE host nas running -\nPORT host 0.0.0.0:445 users:((\"smbd\",pid=1,fd=3))\n")
    m = by_name(servers.discover())
    assert list(m) == ["nas"] and m["nas"]["services"][0]["url"] == "smb://10.77.4.8/"


def test_unknown_web_apps_are_named_by_their_page_title():
    assert servers.service("10.0.0.5", 4567, "python", {"scheme": "http", "title": "Webtoon Downloader"})["label"] == "Webtoon Downloader"
    assert servers.service("10.0.0.5", 5173, "python3", {"scheme": "", "title": ""})["label"] == "python3"
    assert servers.service("10.0.0.5", 9999, "", {"scheme": "", "title": ""})["label"] == "Port 9999"
    assert servers.service("10.0.0.5", 5432, "postgres", {"scheme": "", "title": ""}) is None


def test_the_page_checks_each_service_now_and_keeps_renames(home):
    servers.discover()
    servers.rename("10.77.4.52", 2283, "Photos")
    d = servers.listing()
    m = by_name(d)
    assert m["photos"]["services"][0]["label"] == "Photos" and m["photos"]["services"][0]["up"] and m["photos"]["up"]
    assert m["Home"]["services"][0]["up"] is False and m["Home"]["up"]
    servers.rename("10.77.4.52", 2283, "")
    assert by_name(servers.listing())["photos"]["services"][0]["label"] == "Immich"


def test_machines_added_by_hand(home):
    assert not servers.add("", "1.2.3.4")["ok"] and not servers.add("Router", "bad address!")["ok"]
    assert not servers.add("Router", "10.77.4.1", 70000)["ok"] and not servers.add("Router", "10.77.4.1", 80, url="javascript:alert(1)")["ok"]
    assert servers.add("Router", "10.77.4.1", 80, "the ISP box")["ok"]
    d = servers.listing()
    r = by_name(d)["Router"]
    assert r["manual"] and r["note"] == "the ISP box" and r["services"][0]["port"] == 80
    assert not servers.remove("nope")["ok"] and servers.remove(r["id"])["ok"] and "Router" not in by_name(servers.listing())


def test_rosie_can_answer_whats_the_ip_of(home):
    servers.discover()
    servers.add("Printer", "10.77.4.20", 631)
    rows = servers.tool("immich")["servers"]
    assert rows == [{"machine": "photos", "address": "10.77.4.52", "port": 2283, "service": "Immich", "open": "http://10.77.4.52:2283/"}]
    assert len(servers.tool()["servers"]) == 6 and servers.tool("printer")["servers"][0]["address"] == "10.77.4.20"


def test_without_a_home_server_only_added_machines_show(home, monkeypatch):
    monkeypatch.setattr(servers.security_watch, "_ssh_cmd", lambda: None)
    servers.add("Router", "10.77.4.1")
    d = servers.listing()
    assert d["server"] is False and [m["name"] for m in d["machines"]] == ["Router"]
    with pytest.raises(RuntimeError):
        servers.discover()
