#!/usr/bin/env python3
# board #817 why-watch-rolls-back
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slots  # noqa: E402

os.environ.setdefault("ROSIE_AGENT", "watchdog")


def publish_if_due(st: dict):
    # board #817 owner-publish-after-watch
    if not (slots.OPS / "publish.conf").exists() or not st.get("probation_until"):
        return
    try:
        pub = json.loads((slots.OPS / "publish.json").read_text())
    except (OSError, ValueError):
        pub = {}
    live = slots.SLOT[slots.active()]
    head = slots.head(slots.active())
    if not head or pub.get("published", "").startswith(head) or (slots.OPS / "publish.lock").exists():
        return
    if pub.get("last_try_head") == head and time.time() - pub.get("last_try", 0) < 6 * 3600:
        return
    pub.update(last_try_head=head, last_try=time.time())
    (slots.OPS / "publish.json").write_text(json.dumps(pub, indent=1))
    script = live / "ops" / "publish_public.py"
    if script.exists():
        cmd = [str(live / ".venv" / "bin" / "python"), str(script)]
        if shutil.which("systemd-run"):  # board #817 fix-publish-own-unit
            cmd = ["systemd-run", "--user", "--collect", "--quiet", "--unit", f"rosie-publish-{int(time.time())}", f"--working-directory={live}", *cmd]
        subprocess.Popen(cmd, cwd=live, stdin=subprocess.DEVNULL, stdout=open(slots.OPS / "publish.log", "a"), stderr=subprocess.STDOUT,
                         start_new_session=True)


def main():
    st = slots.load_state()
    if (slots.OPS / "stopped").exists():  # board #817 why-stopped-not-crash
        slots.save_state(wd_fails=0)
        return
    if time.time() >= st.get("probation_until", 0):
        slots.save_state(wd_fails=0)
        publish_if_due(st)
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
            if res.get("ok"):
                subprocess.run(["notify-send", "-a", "Rosie", "Rosie went back to the previous version", "The new version stopped answering, so I switched back automatically."], timeout=5)
        except Exception:
            pass
        print(json.dumps(res))


if __name__ == "__main__":
    main()
