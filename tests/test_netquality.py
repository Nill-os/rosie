import json
import subprocess
import time

import pytest

import netquality as nq

LINUX_OK = "\n".join(f"64 bytes from 1.1.1.1: icmp_seq={i} ttl=57 time={t} ms" for i, t in enumerate([10.0, 14.0, 12.0, 20.0], 1))
WIN_OUT = "Reply from 10.0.0.1: bytes=32 time<1ms TTL=64\nReply from 10.0.0.1: bytes=32 time=3ms TTL=64\nRequest timed out.\nRequest timed out."


def fake_run(monkeypatch, out, calls=None):
    def run(argv, **kw):
        if calls is not None:
            calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, out, "")
    monkeypatch.setattr(nq.subprocess, "run", run)


@pytest.fixture(autouse=True)
def tmpfile(tmp_path, monkeypatch):
    monkeypatch.setattr(nq, "FILE", tmp_path / "nq.jsonl")


def test_linux_ping_math(monkeypatch):
    calls = []
    monkeypatch.setattr(nq, "IS_WIN", False)
    fake_run(monkeypatch, LINUX_OK, calls)
    r = nq.ping("1.1.1.1", count=5)
    assert calls[0][:3] == ["ping", "-n", "-c"] and calls[0][-1] == "1.1.1.1"
    assert r["got"] == 4 and r["loss"] == 20.0 and r["avg_ms"] == 14.0 and r["max_ms"] == 20.0
    assert r["jitter_ms"] == round((4 + 2 + 8) / 3, 1)
    print("PASS: linux ping math")


def test_windows_ping_parse(monkeypatch):
    calls = []
    monkeypatch.setattr(nq, "IS_WIN", True)
    fake_run(monkeypatch, WIN_OUT, calls)
    r = nq.ping("10.0.0.1", count=4)
    assert calls[0][:2] == ["ping", "-n"] and "-w" in calls[0]
    assert r["got"] == 2 and r["loss"] == 50.0 and r["avg_ms"] == 2.0 and r["jitter_ms"] == 2.0
    print("PASS: windows ping parse")


def test_all_lost_and_missing_binary(monkeypatch):
    monkeypatch.setattr(nq, "IS_WIN", False)
    fake_run(monkeypatch, "")
    r = nq.ping("1.1.1.1", count=3)
    assert r["got"] == 0 and r["loss"] == 100.0 and r["avg_ms"] is None and r["jitter_ms"] is None

    def boom(*a, **k):
        raise FileNotFoundError("ping")
    monkeypatch.setattr(nq.subprocess, "run", boom)
    assert nq.ping("1.1.1.1", count=3)["loss"] == 100.0
    print("PASS: all lost")


def test_bad_host_rejected(monkeypatch):
    fake_run(monkeypatch, LINUX_OK)
    for h in ("1.1.1.1; rm -rf /", "-f", "example.com", ""):
        with pytest.raises(ValueError):
            nq.ping(h)
    print("PASS: bad host rejected")


def test_gateway_both_os_and_junk(monkeypatch):
    monkeypatch.setattr(nq, "IS_WIN", False)
    fake_run(monkeypatch, "default via 10.0.0.1 dev wlo1 proto dhcp src 10.0.0.117 metric 600\n")
    assert nq.gateway() == "10.0.0.1"
    monkeypatch.setattr(nq, "IS_WIN", True)
    fake_run(monkeypatch, "10.0.0.254\r\n")
    assert nq.gateway() == "10.0.0.254"
    for junk in ("", "default via evil dev x", "nope"):
        fake_run(monkeypatch, junk)
        assert nq.gateway() is None
        monkeypatch.setattr(nq, "IS_WIN", False)
    print("PASS: gateway parse")


def sample(at, loss=0.0, ms=15.0, jit=2.0, gw_loss=0.0, dns=20.0):
    got = 0 if loss == 100.0 else 10
    net = {"host": nq.INTERNET, "sent": 10, "got": got, "loss": loss, "avg_ms": ms if got else None, "jitter_ms": jit if got else None, "max_ms": ms}
    gw = {"host": "10.0.0.1", "sent": 10, "got": 10, "loss": gw_loss, "avg_ms": 2.0, "jitter_ms": 0.5, "max_ms": 3.0}
    return {"at": at, "gw": gw, "net": net, "dns_ms": dns}


def test_save_prunes_to_seven_days(monkeypatch):
    now = 10_000_000.0
    n = int(2 * nq.KEEP_S / nq.EVERY_S)
    nq.FILE.write_text("".join(json.dumps(sample(now - nq.KEEP_S - 3600 - i)) + "\n" for i in range(n)) + "junk\n")
    nq.save(sample(now - 60), now=now)
    rows = nq.load()
    assert len(rows) == 1 and rows[0]["at"] == now - 60
    print("PASS: prune")


def test_report_clean_and_bad(monkeypatch):
    now = time.time()
    for i in range(4):
        nq.save(sample(now - 3600 * (i + 1)), now=now)
    r = nq.report(hours=24, now=now, fresh=False)
    assert r["summary"] == ["Last 24 h: all 4 checks were fine."] and r["bad"] == 0 and r["median_ms"] == 15.0
    nq.save(sample(now - 1800, loss=20.0, ms=200.0, jit=40.0, gw_loss=10.0, dns=None), now=now)
    nq.save(sample(now - 900, loss=10.0), now=now)
    r = nq.report(hours=24, now=now, fresh=False)
    assert r["bad"] == 2 and r["samples"] == 6
    assert r["summary"][0] == "Last 24 h: 2 of 6 checks had problems."
    assert r["worst"]["reasons"] == ["20% packet loss", "slow replies (200 ms)", "unsteady (jitter 40 ms)", "drops on your home network (10%)", "slow DNS"]
    assert r["summary"][1].startswith("Worst at ") and r["summary"][1].endswith("slow DNS.")
    print("PASS: report lines")


def test_report_now_line(monkeypatch):
    now = time.time()
    monkeypatch.setattr(nq, "sample", lambda t=None: sample(now))
    r = nq.report(hours=1, now=now)
    assert r["summary"][0] == "Now: 15 ms to the internet, jitter 2 ms, no loss; home network 2 ms; DNS 20 ms."
    assert r["summary"][1] == "Last 1 h: the one check was fine."
    monkeypatch.setattr(nq, "sample", lambda t=None: sample(now, loss=100.0))
    assert nq.report(hours=1, now=now)["summary"][0] == "Now: no replies from the internet (100% loss)."
    assert nq.report(hours=1, now=now, fresh=False)["now"] is None
    print("PASS: now line")


def test_no_history():
    assert nq.report(fresh=False)["summary"] == ["No history yet."]
    print("PASS: no history")


def test_job_skips_when_off_or_recent(monkeypatch):
    started = []
    monkeypatch.setattr(nq.threading, "Thread", lambda target, **kw: type("T", (), {"start": staticmethod(lambda: started.append(target))})())
    nq.job(None, {"net_quality_on": False})
    assert started == []
    nq.save(sample(time.time() - 60))
    nq.job(None, {})
    assert started == []
    nq.FILE.write_text(json.dumps(sample(time.time() - nq.EVERY_S - 60)) + "\n")
    marks = []
    monkeypatch.setattr(nq, "sample", lambda: sample(time.time()))
    nq.job(None, {}, mark=lambda ok, err=None: marks.append(ok))
    assert len(started) == 1
    started[0]()
    assert marks == [True] and len(nq.load()) == 2 and not nq._busy.locked()
    print("PASS: job")


def test_tool_clamps_and_errors(monkeypatch):
    seen = []
    monkeypatch.setattr(nq, "report", lambda hours: seen.append(hours) or {"ok": 1})
    nq.tool(9999)
    nq.tool(0)
    nq.tool("x")
    assert seen == [168, 1, 24]

    def boom(hours):
        raise OSError("x")
    monkeypatch.setattr(nq, "report", boom)
    assert nq.tool() == {"error": "Couldn't check the internet: OSError"}
    print("PASS: tool")
