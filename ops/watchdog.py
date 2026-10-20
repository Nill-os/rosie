#!/usr/bin/env python3
"""Runs every minute from a systemd timer, OUTSIDE both code slots. After a switch it watches the new version for 30 minutes; if it stops answering, it switches back by itself."""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slots  # noqa: E402


def main():
    st = slots.load_state()
    if (slots.OPS / "stopped").exists():                 # the owner stopped everything on purpose (power.sh stop): not a crash
        slots.save_state(wd_fails=0)
        return
    if time.time() >= st.get("probation_until", 0):
        slots.save_state(wd_fails=0)
        return
    ok = False
    for _ in range(3):
        h = slots.healthz(timeout=4)
        if h and h.get("slot") == slots.active():
            ok = True
            break
        time.sleep(2)
    if ok:
        slots.save_state(wd_fails=0)
        return
    fails = int(st.get("wd_fails", 0)) + 1
    slots.save_state(wd_fails=fails)
    if fails >= 3:
        res = slots.rollback(f"the new version stopped answering ({fails} checks in a row)")
        try:
            subprocess.run(["notify-send", "-a", "Rosie", "Rosie went back to the previous version", "The new version stopped answering, so I switched back automatically."], timeout=5)
        except Exception:
            pass
        print(json.dumps(res))


if __name__ == "__main__":
    main()
