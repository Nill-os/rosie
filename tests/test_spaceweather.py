import pytest

import spaceweather as sw

QUIET = {
    sw.KP_NOW: [{"time_tag": "2026-11-13T23:09:00", "estimated_kp": 1.0}, {"time_tag": "2026-11-13T23:10:00", "kp_index": 0, "estimated_kp": 0.33, "kp": "0Z"}],
    sw.KP_FORECAST: [{"time_tag": "2026-11-14T00:00:00", "kp": 2.0, "observed": "predicted"}, {"time_tag": "2026-11-13T00:00:00", "kp": 7.0, "observed": "observed"}],
    sw.SCALES: {"-1": {"R": {"Scale": "3"}}, "0": {"R": {"Scale": "0"}, "S": {"Scale": "0"}, "G": {"Scale": None}}},
    sw.FLUX: [{"time_tag": "2026-11-13T20:00:00", "flux": 119.0}, {"time_tag": "2026-10-03T17:00:00", "flux": 93.0}],
    sw.FLARE: [{"current_class": "B7.3", "max_class": "C1.4", "max_time": "2026-11-13T22:01:00Z"}],
}


def feed(monkeypatch, data, calls=None):
    def get(path):
        if calls is not None:
            calls.append(path)
        v = data[path]
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(sw, "_get", get)
    sw._cache.update(at=0.0, data=None)


def test_quiet_day(monkeypatch):
    feed(monkeypatch, QUIET)
    d = sw.summary(fresh=True)
    assert d["kp"] == 0.33 and d["sfi"] == 119.0 and d["r"] == 0 and d["g"] == 0 and d["missing"] == []
    assert d["kp_max_forecast"] == 2.0 and d["flare_max"] == "C1.4"
    lines = sw.verdict(d)
    assert lines[0] == "Geomagnetic: Quiet, Kp 0.3"
    assert "Aurora unlikely outside high latitudes" in lines
    assert any(x.startswith("Mid HF (20 to 15 m) good") for x in lines)
    assert not any("flare" in x.lower() or "forecast" in x.lower() or "blackout" in x.lower() for x in lines)
    print("PASS: quiet day")


def test_storm_with_high_flux(monkeypatch):
    data = dict(QUIET)
    data[sw.KP_NOW] = [{"time_tag": "t", "estimated_kp": 6.0}]
    data[sw.FLUX] = [{"time_tag": "2026-11-13T20:00:00", "flux": 160.0}]
    data[sw.KP_FORECAST] = [{"time_tag": "2026-11-15T03:00:00", "kp": 6.67, "observed": "predicted"}]
    feed(monkeypatch, data)
    lines = sw.verdict(sw.summary(fresh=True))
    assert "Geomagnetic: Moderate storm (G2), Kp 6.0" in lines
    assert "Aurora possible in the northern US" in lines
    assert any(x.startswith("Upper HF (15 to 10 m) likely open; storm may degrade HF") for x in lines)
    assert "Storm forecast: Kp up to 6.7 around 2026-11-15T03:00:00" in lines
    print("PASS: storm lines")


def test_blackout_radiation_and_big_flare(monkeypatch):
    data = dict(QUIET)
    data[sw.SCALES] = {"0": {"R": {"Scale": "3"}, "S": {"Scale": "1"}, "G": {"Scale": "0"}}}
    data[sw.FLARE] = [{"current_class": "M2.1", "max_class": "X1.2", "max_time": "2026-11-13T22:01:00Z"}]
    feed(monkeypatch, data)
    lines = sw.verdict(sw.summary(fresh=True))
    assert "Wide HF blackout on the sunlit side (R3)" in lines
    assert "Solar radiation storm S1: polar HF paths affected" in lines
    assert "Recent flare X1.2 at 2026-11-13T22:01:00Z" in lines
    print("PASS: blackout lines")


def test_bad_types_are_dropped(monkeypatch):
    data = dict(QUIET)
    data[sw.KP_NOW] = [{"time_tag": "t", "estimated_kp": "5"}]
    data[sw.FLARE] = [{"current_class": "Z9", "max_class": True, "max_time": "x"}]
    data[sw.SCALES] = {"0": {"R": {"Scale": "99"}, "S": {"Scale": 4}, "G": "x"}}
    data[sw.FLUX] = [{"time_tag": "t", "flux": True}]
    feed(monkeypatch, data)
    d = sw.summary(fresh=True)
    assert d["kp"] is None and d["flare_now"] is None and d["flare_max"] is None and d["flare_max_at"] is None
    assert d["r"] == 0 and d["s"] == 0 and d["g"] == 0 and d["sfi"] is None
    print("PASS: bad types dropped")


def test_one_endpoint_down_is_listed(monkeypatch):
    data = dict(QUIET)
    data[sw.FLUX] = ValueError("HTTP 503")
    feed(monkeypatch, data)
    out = sw.tool()
    assert out["missing"] == ["flux"] and out["sfi"] is None
    assert not any("HF (" in x for x in out["summary"]) and out["summary"][0].startswith("Geomagnetic")
    print("PASS: missing endpoint listed")


def test_everything_down(monkeypatch):
    feed(monkeypatch, {k: OSError("down") for k in QUIET})
    out = sw.tool()
    assert out["summary"] == ["NOAA space weather is not reachable right now."]
    assert sorted(out["missing"]) == ["flare", "flux", "forecast", "kp", "scales"]
    print("PASS: all down")


def test_cache_reused_then_refetched(monkeypatch):
    calls = []
    feed(monkeypatch, QUIET, calls)
    sw.summary(now=1000.0)
    sw.summary(now=1000.0 + sw.CACHE_S - 1)
    assert len(calls) == 5
    sw.summary(now=1000.0 + sw.CACHE_S + 1)
    assert len(calls) == 10
    print("PASS: cache")


def test_get_rejects_bad_status_and_size(monkeypatch):
    class R:
        def __init__(self, code, n):
            self.status_code, self.content = code, b"x" * n

        def json(self):
            return []
    monkeypatch.setattr(sw.httpx, "get", lambda *a, **k: R(500, 1))
    with pytest.raises(ValueError):
        sw._get(sw.KP_NOW)
    monkeypatch.setattr(sw.httpx, "get", lambda *a, **k: R(200, sw.MAX_BYTES + 1))
    with pytest.raises(ValueError):
        sw._get(sw.KP_NOW)
    print("PASS: _get guards")
