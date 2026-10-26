# board #764 owner-shut-down-all
import os
import subprocess
import time
from pathlib import Path

import testguard

OPS = Path(os.environ.get("ROSIE_OPS_DIR") or Path.home() / "rosie-ops")
SCRIPT = OPS / "power.sh"
FLAG = OPS / "stopped"


def status() -> dict:
    if not SCRIPT.exists():
        return {"error": f"{SCRIPT} is missing (run ops/install.sh)"}
    try:
        out = subprocess.run([str(SCRIPT), "status"], capture_output=True, text=True, timeout=20, env=_bus()).stdout  # board #764 quirk-status-needs-user-bus
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return dict(line.split(" ", 1) for line in out.splitlines() if " " in line)


def _bus() -> dict:
    import pc_tools
    return pc_tools.user_bus_env()


def shutdown_all(run=subprocess.run) -> dict:
    if testguard.active():
        return {"started": False, "note": "not in tests"}
    if not SCRIPT.exists():
        return {"started": False, "error": f"{SCRIPT} is missing (run ops/install.sh)"}
    r = run(["systemd-run", "--user", "--collect", "--quiet", f"--unit=rosie-shutdown-{int(time.time())}",
             "--setenv=ROSIE_PORT=" + os.environ.get("ROSIE_PORT", "8765"), "/bin/bash", "-c", f"sleep 2; exec {SCRIPT} stop"],
            capture_output=True, text=True, timeout=20, env=_bus())
    if r.returncode:
        return {"started": False, "error": (r.stderr or r.stdout).strip()[:200] or "systemd-run failed"}
    return {"started": True}


def clear_flag():
    try:
        testguard.check(FLAG)
        FLAG.unlink(missing_ok=True)
    except (OSError, RuntimeError):
        pass
