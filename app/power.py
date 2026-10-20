"""Shut down all (owner 2026-10-19): one button stops Agent Zero, her memory graph, push-to-talk, the overlay, the AI models and
Rosie herself, through ~/rosie-ops/power.sh (the same script the desktop launcher uses to start everything again).

The stop runs as its own short systemd job (systemd-run --user), outside Rosie's process group: stopping Rosie last can't cut
the rest short. power.sh leaves a "stopped on purpose" flag so the update watchdog doesn't mistake it for a crash; Rosie removes
the flag when she starts."""
import os
import subprocess
import time
from pathlib import Path

import testguard

OPS = Path(os.environ.get("ROSIE_OPS_DIR") or Path.home() / "rosie-ops")
SCRIPT = OPS / "power.sh"
FLAG = OPS / "stopped"


def status() -> dict:
    """{name: state} from power.sh status (containers, helper services, Rosie)."""
    if not SCRIPT.exists():
        return {"error": f"{SCRIPT} is missing (run ops/install.sh)"}
    try:
        out = subprocess.run([str(SCRIPT), "status"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return dict(line.split(" ", 1) for line in out.splitlines() if " " in line)


def shutdown_all(run=subprocess.run) -> dict:
    """Start the stop in the background and return at once (the page shows a goodbye; Rosie goes away a few seconds later)."""
    if testguard.active():
        return {"started": False, "note": "not in tests"}
    if not SCRIPT.exists():
        return {"started": False, "error": f"{SCRIPT} is missing (run ops/install.sh)"}
    r = run(["systemd-run", "--user", "--collect", "--quiet", f"--unit=rosie-shutdown-{int(time.time())}",
             "--setenv=ROSIE_PORT=" + os.environ.get("ROSIE_PORT", "8765"), "/bin/bash", "-c", f"sleep 2; exec {SCRIPT} stop"],
            capture_output=True, text=True, timeout=20)
    if r.returncode:
        return {"started": False, "error": (r.stderr or r.stdout).strip()[:200] or "systemd-run failed"}
    return {"started": True}


def clear_flag():
    """Rosie is starting: whatever stopped her before is over."""
    try:
        testguard.check(FLAG)
        FLAG.unlink(missing_ok=True)
    except (OSError, RuntimeError):
        pass
