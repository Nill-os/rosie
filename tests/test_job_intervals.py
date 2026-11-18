import pytest

import jobs

EXPECTED = {"scheduler": 120, "brief_scheduler": 120}


def _interval(name):
    rows = [s for s in jobs.MARKED if s[0] == name]
    assert len(rows) == 1
    return rows[0][3]


@pytest.mark.parametrize("name,seconds", sorted(EXPECTED.items()))
def test_listed_interval_matches_loop(name, seconds):
    assert _interval(name) == seconds
    print(f"PASS: {name} interval is {seconds}")


def test_intervals_not_below_heartbeat():
    assert all(v >= jobs.HEARTBEAT_S for v in EXPECTED.values())
    print("PASS: listed intervals are not below the heartbeat")
