import re
from pathlib import Path


def test_the_model_eval_keeps_its_turns_out_of_the_live_trace_file():
    src = (Path(__file__).parent / "model_eval.py").read_text(encoding="utf-8")
    head = src.split('if __name__ == "__main__":')[0]
    assert re.search(r"^traces\.path = lambda: TMP / \"traces\.jsonl\"$", head, re.M)
    assert head.index("traces.path = lambda") < head.index("def trace(")
    print("PASS: the model eval points the trace file at its own temp folder before any turn runs")
