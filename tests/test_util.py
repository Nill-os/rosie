import datetime as dt

import util


def test_now_iso_is_seconds_resolution_local():
    s = util.now_iso()
    parsed = dt.datetime.fromisoformat(s)
    assert parsed.tzinfo is None
    assert "." not in s
    assert abs((dt.datetime.now() - parsed).total_seconds()) < 5


def test_now_dt_is_aware_and_current():
    d = util.now_dt()
    assert d.tzinfo is not None
    assert abs((dt.datetime.now().astimezone() - d).total_seconds()) < 5
