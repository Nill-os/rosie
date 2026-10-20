"""Tools the assistant builds for itself. When nothing it has can do a job, it writes a small Python program, saves it here, runs it, and can reuse it later.
They run as YOU (no sudo) with a time limit. Code that mentions credential stores or this app's secrets is refused. Saved in ~/assistant/data/user_tools (readable and editable by you)."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "user_tools"
SITE = DIR / "site"                         # packages the tools asked for, kept apart from the app's own
TIMEOUT = 120
FORBIDDEN = re.compile(r"(\.ssh|\.gnupg|\.aws|\.kube|\.password-store|\.mozilla|google-chrome|chromium|keyrings|\.claude|\.codex|\.gemini|\.config/(gcloud|gh)|/secrets\b|session_key|access_code|google_token|google_client|gmail_\w+\.json|id_rsa|id_ed25519|\.netrc|\.git-credentials|\.env\b|wallet)", re.I)
NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
PKG = re.compile(r"^[A-Za-z0-9_.\-\[\]=<>~! ,]{1,120}$")


def _meta(p):
    try:
        return json.loads(p.with_suffix(".json").read_text())
    except (OSError, ValueError):
        return {}


def list_tools():
    DIR.mkdir(parents=True, exist_ok=True)
    return [{"name": p.stem, "description": _meta(p).get("description", ""), "usage": _meta(p).get("usage", "")} for p in sorted(DIR.glob("*.py"))]


def build_tool(name, description, code, usage="", requirements=""):
    """Save a new tool (or replace one). It is not run yet."""
    name = (name or "").strip().lower()
    if not NAME.match(name):
        return {"error": "Tool name must be lowercase letters, digits and underscores (3-40 chars)."}
    if len(code) > 40000:
        return {"error": "That program is too long. Build it in smaller pieces."}
    bad = FORBIDDEN.search(code)
    if bad:
        return {"error": f"Refused: the code mentions '{bad.group(0)}', a credential store or secret. Tools may not touch those."}
    DIR.mkdir(parents=True, exist_ok=True)
    note = ""
    if requirements.strip():
        if not PKG.match(requirements):
            return {"error": "Package names only (like 'requests beautifulsoup4')."}
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(SITE), *requirements.split()], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            return {"error": "Could not install the packages: " + (r.stderr or r.stdout)[-300:]}
        note = " Packages installed."
    p = DIR / f"{name}.py"
    p.write_text(code)
    p.with_suffix(".json").write_text(json.dumps({"description": description[:300], "usage": usage[:300], "built": time.strftime("%Y-%m-%d %H:%M")}))
    return {"status": "saved", "name": name, "note": "Saved." + note + " Now run it with run_user_tool and check the output."}


def run_tool(name, args=""):
    """Run a saved tool. Input is passed as one text argument and on stdin; its printed output comes back."""
    name = (name or "").strip().lower()
    p = DIR / f"{name}.py"
    if not NAME.match(name) or not p.exists():
        return {"error": f"No tool called '{name}'. Saved tools: {', '.join(t['name'] for t in list_tools()) or 'none'}."}
    if FORBIDDEN.search(p.read_text()) or FORBIDDEN.search(str(args)):
        return {"error": "Refused: this touches a credential store or secret."}
    env = {**os.environ, "PYTHONPATH": str(SITE), "PATH": f"{Path.home()}/.local/bin:/usr/bin:/bin"}
    for k in list(env):
        if re.search(r"(TOKEN|SECRET|KEY|PASSWORD)", k, re.I):
            env.pop(k)                                   # secrets in the environment never reach a tool
    try:
        r = subprocess.run([sys.executable, str(p), str(args)], input=str(args), capture_output=True, text=True, timeout=TIMEOUT, env=env, cwd=str(DIR))
    except subprocess.TimeoutExpired:
        return {"error": f"Stopped: it ran longer than {TIMEOUT} seconds."}
    out = (r.stdout or "")[-4000:]
    return {"exit_code": r.returncode, "output": out, **({"errors": r.stderr[-1500:]} if r.returncode else {})}
