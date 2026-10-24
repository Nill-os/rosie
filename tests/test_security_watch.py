"""Rosie's security watch (security_watch.py): what she counts as a hole, what she patches by herself, and what waits for the owner."""
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import config  # noqa: E402
import corner  # noqa: E402
import security_watch as sw  # noqa: E402

NIGHT = dt.datetime(2026, 10, 23, 3, 30).astimezone()
REAL_INVENTORY = sw.inventory
INV = {
    "this PC": {"os": "this PC", "security": ["libssl3t64"], "apps": {"ollama": "0.40.6"},
                "packages": [("Rosie", "Jinja2", "3.1.2"), ("Rosie", "fastapi", "0.142.2"), ("~/tool", "requests", "2.0.0")]},
    "home server": {"os": "Debian", "security": [], "apps": {"proxmox-ve": "9.2.21"}, "packages": []},
    "immich (container 101)": {"os": "Debian", "security": ["openssl", "libc6"], "apps": {"immich": "2.4.1"}, "packages": [], "ct": "101"},
}
OSV_VULN = {"id": "GHSA-jin", "summary": "Jinja2 sandbox breakout", "database_specific": {"severity": "HIGH"},
            "affected": [{"package": {"name": "jinja2", "ecosystem": "PyPI"}, "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "3.1.6"}]}]}]}
REQ_VULN = {"id": "GHSA-req", "summary": "requests leaks", "database_specific": {"severity": "MODERATE"},
            "affected": [{"package": {"name": "requests", "ecosystem": "PyPI"}, "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.32.0"}]}]}]}
IMMICH = [{"ghsa_id": "GHSA-imm", "cve_id": "CVE-2026-1", "severity": "high", "published_at": "2026-09-28T00:00:00Z", "summary": "SVG upload",
           "html_url": "https://github.com/x", "vulnerabilities": [{"vulnerable_version_range": "<= v3.1.0", "patched_versions": "v3.2.0"}]},
          {"ghsa_id": "GHSA-old", "cve_id": None, "severity": "low", "published_at": "2024-01-01T00:00:00Z", "summary": "only old",
           "html_url": "", "vulnerabilities": [{"vulnerable_version_range": "< 1.90.0", "patched_versions": "1.90.0"}]}]
KEV = {"vulnerabilities": [
    {"cveID": "CVE-2026-9", "vendorProject": "Proxmox", "product": "Virtual Environment", "vulnerabilityName": "PVE RCE", "dateAdded": "2026-10-22",
     "shortDescription": "Remote code execution."},
    {"cveID": "CVE-2026-8", "vendorProject": "Arista", "product": "VeloCloud", "vulnerabilityName": "SQLi", "dateAdded": "2026-10-22", "shortDescription": "x"},
    {"cveID": "CVE-2020-1", "vendorProject": "Samba", "product": "Samba", "vulnerabilityName": "old", "dateAdded": "2020-01-01", "shortDescription": "x"}]}
NVD = {"vulnerabilities": [{"cve": {"id": "CVE-2026-7", "vulnStatus": "Analyzed", "descriptions": [{"lang": "en", "value": "XSS in Proxmox VE."}],
                                    "metrics": {"cvssMetricV31": [{"cvssData": {"baseSeverity": "MEDIUM"}}]}}}]}


def transport(seen):
    def handler(req: httpx.Request):
        seen.append(str(req.url))
        u = str(req.url)
        if u.endswith("/querybatch"):
            qs = json.loads(req.content)["queries"]
            vulns = {"jinja2": [{"id": "GHSA-jin", "modified": "1"}], "requests": [{"id": "GHSA-req", "modified": "1"}]}
            return httpx.Response(200, json={"results": [{"vulns": vulns.get(q["package"]["name"], [])} for q in qs]})
        if "/vulns/" in u:
            return httpx.Response(200, json={"GHSA-jin": OSV_VULN, "GHSA-req": REQ_VULN}[u.rsplit("/", 1)[1]])
        if "immich-app/immich" in u:
            return httpx.Response(200, json=IMMICH)
        if "api.github.com" in u:
            return httpx.Response(200, json=[])
        if "cisa.gov" in u:
            return httpx.Response(200, json=KEV)
        if "nvd.nist.gov" in u:
            return httpx.Response(200, json=NVD if "Proxmox" in u else {"vulnerabilities": []})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.fixture
def lab(monkeypatch, tmp_path):
    lab = {"inbox": [], "ssh": [], "started": [], "seen": []}
    monkeypatch.setattr(sw, "STATE", tmp_path / "security_watch.json")
    monkeypatch.setattr(sw, "state", {"running": False, "doing": "", "last_error": ""})
    monkeypatch.setattr(sw, "NVD_GAP_S", 0)
    monkeypatch.setattr(sw, "inventory", lambda: {"machines": json.loads(json.dumps(INV)), "problems": []})
    monkeypatch.setattr(sw, "_ssh_cmd", lambda: ["ssh", "host"])
    monkeypatch.setattr(sw, "_run", lambda argv, timeout, stdin=None: lab["ssh"].append((argv[-1], stdin)) or "")
    monkeypatch.setattr(sw, "_start_self_patch", lambda plan, d: lab["started"].append(plan))
    import inbox
    monkeypatch.setattr(inbox, "add", lambda *a, **k: lab["inbox"].append((a, k)) or len(lab["inbox"]))
    monkeypatch.setattr(config, "load", lambda: {**config.DEFAULTS, "security_autopatch": True})
    lab["client"] = lambda: httpx.Client(transport=transport(lab["seen"]))
    return lab


def titles(lab):
    return [a[1] for a, _ in lab["inbox"]]


# ---------------------------------------------------------------- versions
@pytest.mark.parametrize("version,rng,hit", [
    ("2.4.1", "<= v3.1.0", True), ("3.2.0", "<=3.1.0", False), ("3.2.2", "v3.2.2", True), ("3.2.3", "v3.2.2", False),
    ("1.5", ">= 1.0, < 1.6", True), ("1.6", ">= 1.0, < 1.6", False), ("0.58.0 (abc)", "< 0.58.1", True), ("2.4.1", "", None),
    ("weird", "< 1.0", None), ("1.0", "~> 1.0", None)])
def test_advisory_ranges(version, rng, hit):
    assert sw.in_range(version, rng) is hit


@pytest.mark.parametrize("version,rng,patched,hit", [
    ("6.7.1", ">= 6.0", "6.7.1", False), ("6.7.0", ">= 6.0", "6.7.1", True), ("6.7.1", "<= 6.7.1", "6.8", True), ("6.7.1", ">=6.0", ">=6.7", False),
    ("6.6.1", "6.6", ">=v6.6.1", False), ("6.6", "6.6", ">=v6.6.1", True), ("2.4.1", "<= v3.1.0", "None", True), ("2.4.1", "<= v3.1.0", "", True)])
def test_an_advisory_counts_only_before_its_fix(version, rng, patched, hit):
    assert sw.affected(version, rng, patched) is hit


def test_the_lowest_fix_above_the_installed_version():
    v = {"affected": [{"package": {"name": "Py_Thing"}, "ranges": [{"events": [{"introduced": "0"}, {"fixed": "1.5"}, {"introduced": "2.0"}, {"fixed": "2.3"}]}]},
                      {"package": {"name": "other"}, "ranges": [{"events": [{"fixed": "2.1"}]}]}]}
    assert sw.fixed_version(v, "py-thing", "2.1") == "2.3" and sw.fixed_version(v, "py.thing", "1.0") == "1.5"
    assert sw.fixed_version(v, "py-thing", "2.3") == ""


def test_inventory_lines_from_a_container():
    text = ("OS Debian GNU/Linux 13\nSEC openssl\nSEC bad;rm -rf\nAPP immich {\"major\":2,\"minor\":4,\"patch\":1}\nAPP pi-hole v6.4.3\n"
            "APP navidrome 0.58.0 (abc)\nAPP broken \nPKG /opt/app/venv flask 3.0.0\n")
    m = sw.parse_inventory("media (container 112)", text)
    assert m["os"] == "Debian GNU/Linux 13" and m["security"] == ["openssl"]                  # a name that isn't a package name is dropped
    assert m["apps"] == {"immich": "2.4.1", "pi-hole": "6.4.3", "navidrome": "0.58.0"}
    assert m["packages"] == [("media (container 112):/opt/app/venv", "flask", "3.0.0")]


def test_container_patches_are_security_packages_only_and_never_prompt():
    cmd = sw.apt_security_cmd(["openssl", "libc6"])
    assert "install --only-upgrade openssl libc6" in cmd and "force-confold" in cmd and "</dev/null" in cmd
    assert "dist-upgrade" not in cmd and "remove" not in cmd
    for bad in (["x; reboot"], ["$(id)"], []):
        with pytest.raises(ValueError):
            sw.apt_security_cmd(bad)


def test_her_own_patch_stays_inside_the_major_version():
    fs = [{"kind": "package", "where": "Rosie", "name": "a", "version": "1.2", "fix": "1.4"},
          {"kind": "package", "where": "Rosie", "name": "a", "version": "1.2", "fix": "1.3"},
          {"kind": "package", "where": "Rosie", "name": "b", "version": "1.9", "fix": "2.0"},          # a major bump waits for the owner
          {"kind": "package", "where": "Rosie", "name": "c", "version": "1.0", "fix": ""},
          {"kind": "package", "where": "~/tool", "name": "d", "version": "1.0", "fix": "1.1"}]
    assert sw.self_patch_plan(fs) == {"a": ("1.2", "1.4")}


# ---------------------------------------------------------------- her own patch
def ok(code=0, out=""):
    return subprocess.CompletedProcess([], code, out, "")


def test_self_patch_installs_tests_restarts_and_checks():
    calls = []
    r = sw.run_self_patch({"jinja2": ("3.1.2", "3.1.6")}, pip=lambda s: calls.append(("pip", s)) or ok(), tests=lambda: calls.append("tests") or ok(),
                          restart=lambda: calls.append("restart"), healthy=lambda: True)
    assert r == {"ok": True, "why": ""} and calls == [("pip", ["jinja2==3.1.6"]), "tests", "restart"]


def test_failed_tests_put_the_old_versions_back_without_a_restart():
    calls = []
    r = sw.run_self_patch({"jinja2": ("3.1.2", "3.1.6")}, pip=lambda s: calls.append(s) or ok(), tests=lambda: ok(1, "F\n1 failed in 3s\n"),
                          restart=lambda: calls.append("restart"), healthy=lambda: True)
    assert not r["ok"] and "tests failed" in r["why"] and "1 failed" in r["why"]
    assert calls == [["jinja2==3.1.6"], ["jinja2==3.1.2"]]


def test_a_bad_restart_puts_the_old_versions_back_and_restarts_again():
    calls, health = [], iter([False, True])
    r = sw.run_self_patch({"jinja2": ("3.1.2", "3.1.6")}, pip=lambda s: calls.append(s) or ok(), tests=lambda: ok(),
                          restart=lambda: calls.append("restart"), healthy=lambda: next(health))
    assert not r["ok"] and "didn't start" in r["why"] and "still" not in r["why"]
    assert calls == [["jinja2==3.1.6"], "restart", ["jinja2==3.1.2"], "restart"]


def test_a_pip_failure_changes_nothing_else():
    calls = []
    r = sw.run_self_patch({"jinja2": ("3.1.2", "3.1.6")}, pip=lambda s: calls.append(s) or ok(1 if len(calls) == 1 else 0), tests=lambda: 1 / 0,
                          restart=lambda: 1 / 0, healthy=lambda: 1 / 0)
    assert not r["ok"] and "pip" in r["why"] and calls == [["jinja2==3.1.6"], ["jinja2==3.1.2"]]


# ---------------------------------------------------------------- the nightly check
def test_the_check_finds_holes_patches_the_containers_and_starts_her_own_patch(lab):
    sw.check(NIGHT, lab["client"]())
    d = sw.load()
    keys = set(d["findings"])
    assert any(k.startswith("osv:Rosie:jinja2:GHSA-jin") for k in keys) and any(k.startswith("osv:~/tool:requests") for k in keys)
    assert any(k.startswith("ghsa:immich (container 101):immich:GHSA-imm") for k in keys) and not any("GHSA-old" in k for k in keys)
    assert lab["ssh"] == [("pct exec 101 -- sh -s", sw.apt_security_cmd(["openssl", "libc6"]) + "\n")]   # only the container, security only
    assert d["machines"]["immich (container 101)"]["security"] == [] and d["patched"][-1]["where"] == "immich (container 101)"
    assert lab["started"] == [{"Jinja2": ("3.1.2", "3.1.6")}]
    t = titles(lab)
    assert any("Being exploited now: CVE-2026-9" in x for x in t) and not any("CVE-2026-8" in x or "CVE-2020-1" in x for x in t)
    assert any("CVE-2026-7" in x for x in t) and any(x.startswith("immich 2.4.1") for x in t) and any("~/tool" in x for x in t)
    assert not any("Python packages: Rosie" in x for x in t)                # she patches those herself
    assert not any("waiting on this PC" in x for x in t)                     # the first night: unattended-upgrades may still get it
    assert any("Security updates installed" in x for x in t)
    assert d["ran_on"] == "2026-10-23" and d["kev_seen"] == ["CVE-2026-9"] and d["nvd_seen"] == ["CVE-2026-7"]


def test_the_next_night_reports_nothing_new_but_still_waiting_updates(lab):
    sw.check(NIGHT, lab["client"]())
    lab["inbox"].clear()
    sw.check(NIGHT + dt.timedelta(days=1), lab["client"]())
    t = titles(lab)
    assert any("1 security updates waiting on this PC" in x for x in t)     # still there a day later
    assert not any("Being exploited" in x or "CVE-2026-7" in x for x in t)


def test_a_patch_that_failed_before_isnt_tried_again_and_goes_to_the_inbox(lab):
    sw.save({**sw.load(), "self_patch": {"plan": {"Jinja2": ["3.1.2", "3.1.6"]}, "done_at": "x", "ok": False, "why": "tests failed"}})
    sw.check(NIGHT, lab["client"]())
    assert lab["started"] == [] and any("Python packages: Rosie" in x for x in titles(lab))


def test_without_autopatch_nothing_is_installed(lab, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {**config.DEFAULTS, "security_autopatch": False})
    sw.check(NIGHT, lab["client"]())
    assert lab["ssh"] == [] and lab["started"] == [] and any("Python packages: Rosie" in x for x in titles(lab))


def test_a_source_that_fails_is_reported_and_the_rest_still_runs(lab):
    def broken():
        def handler(req):
            return httpx.Response(503) if "cisa.gov" in str(req.url) else transport([]).handle_request(req)
        return httpx.Client(transport=httpx.MockTransport(handler))
    sw.check(NIGHT, broken())
    d = sw.load()
    assert any("exploited-now list" in p for p in d["problems"]) and d["findings"]


def test_self_patch_result_is_reported_once(lab):
    sw.save({**sw.load(), "self_patch": {"plan": {"jinja2": ["3.1.2", "3.1.6"]}, "done_at": "2026-10-23T04:00", "ok": True, "reported": False}})
    sw._report_self_patch()
    sw._report_self_patch()
    assert titles(lab) == ["Rosie patched herself"] and "jinja2 3.1.2 -> 3.1.6" in lab["inbox"][0][0][2]


# ---------------------------------------------------------------- when it runs, and the owner's buttons
def test_due_only_at_night_once_and_while_the_owner_is_away(lab, monkeypatch, tmp_path):
    monkeypatch.setattr(corner, "STAGE_FLAG", tmp_path / "no-stage")
    monkeypatch.setattr(corner, "owner_recent", lambda s: False)
    assert sw.due({}, NIGHT) is None
    assert sw.due({}, NIGHT.replace(hour=14)) == "outside the night window"
    assert sw.due({"ran_on": "2026-10-23"}, NIGHT) == "already checked today"
    monkeypatch.setattr(corner, "owner_recent", lambda s: True)
    assert sw.due({}, NIGHT) == "the owner was here recently"
    monkeypatch.setattr(corner, "owner_recent", lambda s: False)
    (tmp_path / "no-stage").write_text("")
    assert sw.due({}, NIGHT) == "a stage check is running"


def test_owner_patches_run_in_the_background_and_are_reported(lab):
    sw.save({**sw.load(), "machines": {"pihole (container 111)": {"ct": "111", "apps": {"pi-hole": "6.4.3"}}}})
    assert sw.patch("rm -rf")["ok"] is False
    r = sw.patch("pi-hole")
    assert r["ok"]
    for _ in range(100):
        if not sw.state["running"]:
            break
        time.sleep(0.02)
    assert lab["ssh"][-1] == ("sh -s", "pct exec 111 -- sh -c 'pihole -up </dev/null'\n")
    assert sw.load()["patched"][-1]["ok"] and titles(lab) == ["Update Pi-hole: done"]


def test_status_for_settings(lab):
    sw.check(NIGHT, lab["client"]())
    s = sw.status()
    assert s["on"] and s["autopatch"] and s["holes"] >= 3 and s["serious"] >= 2 and "immich on immich (container 101)" in s["apps"]
    assert s["waiting"] == {"this PC": 1} and [p["target"] for p in s["patches"]] == ["server"]     # no Pi-hole here: no Pi-hole button
    assert s["found"] == ["Immich", "Ollama", "Proxmox"]


# ---------------------------------------------------------------- nothing owner-specific: any setup (owner 2026-10-23: sharing Rosie)
@pytest.mark.parametrize("image,app", [
    ("ghcr.io/immich-app/immich-server:v2.4.1", ("immich", "2.4.1")), ("pihole/pihole:2026.10.0", ("pi-hole", "2026.10.0")),
    ("deluan/navidrome:latest", ("navidrome", "")), ("falkordb/falkordb-server:v4.22.2", ("falkordb", "4.22.2")),
    ("localhost:5000/agent-zero", ("agent-zero", "")), ("postgres:16", ("", ""))])
def test_apps_are_recognized_from_their_docker_images(image, app):
    assert sw.image_app(image) == app


def test_she_searches_for_the_apps_she_found():
    assert sw.watch_words({"pi-hole", "pi-hole-ftl", "immich", "proxmox-ve"}) == ["Immich", "Pi-hole", "Proxmox"]
    assert sw.watch_words(set()) == []


def test_a_plain_server_without_proxmox_is_checked_by_itself(lab, monkeypatch):
    monkeypatch.setattr(sw, "inventory", REAL_INVENTORY)
    calls = []
    def run(argv, timeout, stdin=None):
        calls.append(argv[-1])
        return "OS Ubuntu 24.04\nSEC openssl\nIMG pihole/pihole:2026.10.0\n" if argv[-1] == "sh -s" else ""
    monkeypatch.setattr(sw, "_run", run)
    monkeypatch.setattr(sw, "_local_venvs", lambda: [])
    monkeypatch.setattr(sw.shutil, "which", lambda name: None)
    monkeypatch.setattr(sw.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("no ollama")))
    inv = sw.inventory()
    assert "pct list" not in calls and inv["machines"]["home server"]["apps"] == {"pi-hole": "2026.10.0"}
    assert inv["machines"]["home server"]["security"] == ["openssl"] and list(inv["machines"]) == ["this PC", "home server"]


@pytest.mark.parametrize("ssh,key,ok", [("root@192.168.1.10", "~/.ssh/id_ed25519", True), ("", "", True), ("root@host; rm -rf /", "", False),
                                        ("me@srv", "/etc/shadow", False), ("me@srv", "~/.ssh/../../x", False)])
def test_the_home_server_setting_is_checked(lab, monkeypatch, ssh, key, ok):
    saved = []
    monkeypatch.setattr(config, "save", saved.append)
    assert sw.set_server(ssh, key)["ok"] is ok and bool(saved) is ok
