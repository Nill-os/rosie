from pathlib import Path

import traces

REAL = Path(traces.__file__).resolve().parent.parent / "data" / "traces.jsonl"


def test_default_trace_does_not_touch_the_real_file():
    before = REAL.stat().st_size if REAL.exists() else None
    traces.default.start(987654321, "isolation probe")
    traces.default.end("ok")
    after = REAL.stat().st_size if REAL.exists() else None
    assert traces.path() != REAL
    assert after == before
    print("PASS: trace writes stay out of the real data folder")
