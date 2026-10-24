"""Rosie's security watch (owner 2026-10-23: "add a zero day search ... that happens every day so she can patch herself or any of my
services like proxmox, immich, custom apps").

Once a night (3:00 to 6:00, while the owner is away) she takes stock of what runs here and on the home server (Settings > Security:
any Debian/Ubuntu server she can reach by SSH key; on a Proxmox host also every running container), then looks it up:
  - Python packages: her own (.venv), the owner's other apps on this PC (~/*/.venv, ~/*/venv) and the apps in the home server's
    containers (any Python environment under /opt, /srv, /home, /root) -> OSV.dev, with the version each hole is fixed in.
  - Apps she recognizes (installed, or as Docker images): Immich, Pi-hole, Navidrome, Ollama, FalkorDB, Agent Zero -> their GitHub
    security advisories. KEV and NVD are searched for the apps she found, plus common system parts (kernel, OpenSSH, Docker ...).
  - System packages waiting with a security fix: this PC, the home server and, on a Proxmox host, each container (apt).
  - Zero-days: CISA's list of holes that are being exploited right now (KEV) and new CVEs (NVD) that name something she watches.

What she patches by herself (Settings > Security, "Patch by herself"; on by default):
  - Her own Python packages: in a separate process (it outlives her restart), to the lowest version with the fix in the same major
    version; then her whole test suite, a restart and a health check. A failed test or a bad restart puts the old versions back.
  - The containers' system security updates (security fixes only, like unattended-upgrades; nothing else is upgraded).
What waits for the owner (an Inbox item; some have a Patch button in Settings): the home server itself (its updates may need a
reboot), Pi-hole, app updates (Immich, Navidrome: steps in the Inbox item), this PC's system packages (they need your password), and
the other apps' Python packages (a version bump can break an app she can't test).
Nothing is ever deleted, rebooted or downgraded on the server. Everything is kept in data/security_watch.json.
"""
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

import config
import corner
import testguard

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "security_watch.json"
RUN_FROM, RUN_TO = dt.time(3, 0), dt.time(6, 0)
AWAY_S = 1800
PORT = int(os.environ.get("ROSIE_PORT") or 8765)
OSV = "https://api.osv.dev/v1"
GITHUB = "https://api.github.com/repos/{}/security-advisories"
KEV = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
NVD = "https://services.nvd.nist.gov/rest/json/cves/2.0"
NVD_GAP_S = 7                               # NVD allows 5 requests in 30 s without a key
FIRST_LOOKBACK_D = 14                       # the first run reads this far back in KEV and NVD (not their whole history)
MAX_OSV_DETAILS = 400
APPS = {                                    # app name (as the inventory reports it) -> its GitHub repository
    "immich": "immich-app/immich", "pi-hole": "pi-hole/pi-hole", "pi-hole-web": "pi-hole/web", "pi-hole-ftl": "pi-hole/FTL",
    "navidrome": "navidrome/navidrome", "ollama": "ollama/ollama", "falkordb": "FalkorDB/FalkorDB", "agent-zero": "agent0ai/agent-zero"}
NAMES = {"immich": "Immich", "pi-hole": "Pi-hole", "pi-hole-web": "Pi-hole", "pi-hole-ftl": "Pi-hole", "navidrome": "Navidrome",
         "ollama": "Ollama", "falkordb": "FalkorDB", "agent-zero": "Agent Zero", "proxmox-ve": "Proxmox"}   # how CVEs name them
IMAGES = {"immich-server": "immich", "pihole/pihole": "pi-hole", "navidrome": "navidrome", "ollama/ollama": "ollama",
          "falkordb": "falkordb", "agent-zero": "agent-zero"}                                           # Docker image -> app
STEPS = {                                   # how the owner updates an app she can't update safely by herself
    "immich": "Update Immich the way it was installed: a Proxmox helper-script container: type update in its console; Docker: "
              "docker compose pull && docker compose up -d in its folder.",
    "navidrome": "Install the new release from github.com/navidrome/navidrome/releases (Docker: pull the new image) and restart it.",
    "ollama": "On that PC: curl -fsSL https://ollama.com/install.sh | sh (Windows and Mac: the Ollama app updates itself).",
    "falkordb": "Change the memory graph container's image tag to the fixed version and recreate it.",
    "agent-zero": "Rebuild or pull the newest Agent Zero image."}
for _p in ("pi-hole", "pi-hole-web", "pi-hole-ftl"):
    STEPS[_p] = "Press Update Pi-hole in Rosie's Settings > Security (it runs pihole -up), or run pihole -up where Pi-hole runs."
# CISA KEV: what she always watches for (a word in the vendor/product), plus every app she finds (watch_words)
KEV_WATCH = ("linux kernel", "debian", "ubuntu", "canonical", "docker", "chromium", "chrome", "openssh", "python", "samba", "redis",
             "fastapi", "uvicorn", "wireguard", "tailscale", "systemd", "polkit", "glibc", "sudo", "openssl", "curl", "git", "nginx",
             "lxc", "qemu", "windows")
PKG_NAME = re.compile(r"^[a-z0-9][a-z0-9+.:~-]*$")
LINUX = sys.platform.startswith("linux")
SERVER = "home server"                      # the machine at proxmox_ssh (a Proxmox host, or any Debian/Ubuntu server)

_lock = threading.Lock()
state = {"running": False, "doing": "", "last_error": ""}

# The inventory script, run on the home server and in each Proxmox container (sh -s, so nothing has to be quoted twice). One line per fact.
INVENTORY_SH = r"""
PATH="$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
. /etc/os-release 2>/dev/null; echo "OS $PRETTY_NAME"
apt-get update -qq >/dev/null 2>&1
apt list --upgradable 2>/dev/null | grep -- '-security' | cut -d/ -f1 | sed 's/^/SEC /'
command -v pveversion >/dev/null && echo "APP proxmox-ve $(pveversion | cut -d/ -f2)"
[ -d /opt/immich ] && echo "APP immich $(curl -s -m5 localhost:2283/api/server/version)"
if [ -f /etc/pihole/versions ]; then . /etc/pihole/versions; echo "APP pi-hole $CORE_VERSION"; echo "APP pi-hole-web $WEB_VERSION"; echo "APP pi-hole-ftl $FTL_VERSION"; fi
command -v navidrome >/dev/null && echo "APP navidrome $(navidrome --version 2>/dev/null | head -1)"
command -v docker >/dev/null && docker ps --format 'IMG {{.Image}}' 2>/dev/null
for c in $(find /opt /srv /home /root -maxdepth 4 -name pyvenv.cfg 2>/dev/null); do
  d=$(dirname "$c"); [ -x "$d/bin/python" ] || continue
  "$d/bin/python" -I -c 'import importlib.metadata as m, sys
for x in m.distributions(): print("PKG", sys.argv[1], x.metadata["Name"], x.version)' "$d" 2>/dev/null
done
true
"""
LIST_PY = 'import importlib.metadata as m\nfor x in m.distributions(): print(x.metadata["Name"], x.version)'


# ---------------------------------------------------------------- the saved record
def load() -> dict:
    try:
        d = json.loads(STATE.read_text())
    except (OSError, ValueError):
        d = {}
    for k, v in (("findings", {}), ("osv_cache", {}), ("kev_seen", []), ("nvd_seen", []), ("patched", []), ("machines", {})):
        d.setdefault(k, v)
    return d


def save(d: dict):
    testguard.check(STATE)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(STATE)


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _iso(t: dt.datetime | None = None) -> str:
    return (t or _now()).isoformat(timespec="seconds")


# ---------------------------------------------------------------- versions
def _version(v: str):
    """packaging Version of '2.4.1', 'v6.4.3', '0.58.0 (abc123)', {"major":2,...}; None when it isn't one."""
    from packaging.version import InvalidVersion, Version
    if isinstance(v, dict):
        v = ".".join(str(v.get(k, 0)) for k in ("major", "minor", "patch"))
    m = re.match(r"\s*v?(\d[\w.+-]*)", str(v or ""))
    try:
        return Version(m.group(1)) if m else None
    except InvalidVersion:
        return None


def in_range(version: str, rng: str) -> bool | None:
    """Is `version` inside a GitHub advisory range ('< 1.2.3', '>= 1.0, <= 2.1', 'v3.2.2' = that one version)? None = can't tell."""
    have = _version(version)
    if have is None or not (rng or "").strip():
        return None
    for part in rng.split(","):
        m = re.fullmatch(r"\s*(<=|>=|<|>|==|=)?\s*(v?\d[\w.+-]*)\s*", part)
        want = _version(m.group(2)) if m else None
        if want is None:
            return None
        op = m.group(1) or "="
        if not {"<": have < want, "<=": have <= want, ">": have > want, ">=": have >= want}.get(op, have == want):
            return False
    return True


def _pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def affected(version: str, rng: str, patched: str) -> bool:
    """In the advisory's range and not at or past its patched version (some advisories give an open range like '>= 6.0' and put the
    fix only in patched_versions: '6.7.1', '>=6.7', '>=v6.6.1')."""
    if not in_range(version, rng):
        return False
    fix = _version(re.sub(r"^\s*>=?\s*", "", patched or ""))
    return fix is None or _version(version) < fix


def fixed_version(vuln: dict, name: str, version: str) -> str:
    """The lowest OSV 'fixed' version above `version` for package `name` ('' = no fix released)."""
    have, best = _version(version), None
    for a in vuln.get("affected", []):
        if _pep503((a.get("package") or {}).get("name", "")) != _pep503(name):
            continue
        for r in a.get("ranges", []):
            for e in r.get("events", []):
                f = _version(e.get("fixed", ""))
                if f is not None and have is not None and f > have and (best is None or f < best):
                    best = f
    return str(best) if best else ""


def _severity(v: dict) -> str:
    s = str((v.get("database_specific") or {}).get("severity") or v.get("severity") or "").upper()
    return {"MODERATE": "MEDIUM"}.get(s, s) if s in ("LOW", "MODERATE", "MEDIUM", "HIGH", "CRITICAL") else "UNKNOWN"


# ---------------------------------------------------------------- the inventory
def _ssh_cmd() -> list | None:
    cfg = config.load()
    host, key = cfg.get("proxmox_ssh", ""), cfg.get("proxmox_ssh_key", "")
    if not host:
        return None
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", *(["-i", os.path.expanduser(key)] if key else []), host]


def _run(argv: list, timeout: float, stdin: str | None = None) -> str:
    r = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(argv[:3])}...: {(r.stderr or r.stdout).strip()[-200:]}")
    return r.stdout


def parse_inventory(where: str, text: str) -> dict:
    """The INVENTORY_SH lines of one machine -> {os, security: [pkg], apps: {name: version}, packages: [(where, name, version)]}."""
    out = {"os": "", "security": [], "apps": {}, "packages": []}
    for line in text.splitlines():
        kind, _, rest = line.partition(" ")
        if kind == "OS":
            out["os"] = rest.strip()
        elif kind == "SEC" and PKG_NAME.match(rest.strip()):
            out["security"].append(rest.strip())
        elif kind == "APP":
            name, _, v = rest.partition(" ")
            if v.strip().startswith("{"):
                try:
                    v = json.loads(v)
                except ValueError:
                    v = ""
            if _version(v) is not None:
                out["apps"][name] = str(_version(v))
        elif kind == "IMG":
            app, v = image_app(rest.strip())
            if app:
                out["apps"].setdefault(app, v)
        elif kind == "PKG":
            parts = rest.split(" ")
            if len(parts) == 3:
                out["packages"].append((f"{where}:{parts[0]}", parts[1], parts[2]))
    return out


def image_app(image: str) -> tuple[str, str]:
    """('immich', '2.4.1') for 'ghcr.io/immich-app/immich-server:v2.4.1'; version '' for a tag like 'latest' (new advisories only)."""
    name, _, tag = image.rpartition(":") if ":" in image.rsplit("/", 1)[-1] else (image, "", "")
    app = next((a for key, a in IMAGES.items() if key in name.lower()), "")
    return app, str(_version(tag)) if app and _version(tag) else ""


def watch_words(apps) -> list[str]:
    """What KEV and NVD are searched for: the apps she found (by the name CVEs use), and Proxmox when there's a Proxmox host."""
    return sorted({NAMES.get(a, a) for a in apps})


def _local_venvs() -> list[Path]:
    """Rosie's own environment first, then the owner's other apps on this PC (not Rosie's dev copies: they share her packages)."""
    found = [ROOT / ".venv"]
    for cfg in sorted(Path.home().glob("*/.venv/pyvenv.cfg")) + sorted(Path.home().glob("*/venv/pyvenv.cfg")) + sorted(Path.home().glob("*/pyvenv.cfg")):
        venv = cfg.parent
        top = (venv if venv.parent == Path.home() else venv.parent).name
        if _python(venv).exists() and not top.startswith(("rosie-dev-", "assistant")) and top != ROOT.name:
            found.append(venv)
    return found


def _python(venv: Path) -> Path:
    return venv / "bin" / "python" if LINUX or sys.platform == "darwin" else venv / "Scripts" / "python.exe"


def inventory() -> dict:
    """Every machine she can see: {machine: parse_inventory(...)} plus 'problems' (what couldn't be read)."""
    machines, problems = {}, []
    here = {"os": "this PC", "security": [], "apps": {}, "packages": []}
    if shutil.which("apt"):                                                # Debian/Ubuntu; other systems update their own way
        try:
            here["security"] = [ln.split("/")[0] for ln in _run(["apt", "list", "--upgradable"], 60).splitlines() if "-security" in ln]
        except Exception as e:
            problems.append(f"this PC's updates: {e}")
    for venv in _local_venvs():
        where = "Rosie" if venv == ROOT / ".venv" else "~/" + str((venv if venv.parent == Path.home() else venv.parent).relative_to(Path.home()))
        try:
            for ln in _run([str(_python(venv)), "-I", "-c", LIST_PY], 60).splitlines():
                name, _, v = ln.partition(" ")
                here["packages"].append((where, name, v.strip()))
        except Exception as e:
            problems.append(f"{where}: {e}")
    try:
        here["apps"]["ollama"] = str(_version(httpx.get("http://127.0.0.1:11434/api/version", timeout=5).json()["version"]))
    except Exception as e:
        problems.append(f"Ollama's version: {type(e).__name__}")
    if shutil.which("docker"):
        try:
            for ln in _run(["docker", "ps", "--format", "{{.Image}}"], 30).splitlines():
                app, v = image_app(ln.strip())
                if app:
                    here["apps"].setdefault(app, v)
        except Exception as e:
            problems.append(f"docker: {e}")
    machines["this PC"] = here
    ssh = _ssh_cmd()
    if ssh:
        try:
            host = parse_inventory("home server", _run(ssh + ["sh -s"], 300, INVENTORY_SH))
            machines[SERVER] = host
            for ln in (_run(ssh + ["pct list"], 60).splitlines()[1:] if "proxmox-ve" in host["apps"] else []):   # Proxmox containers
                f = ln.split()
                if len(f) >= 3 and f[0].isdigit() and f[1] == "running":
                    name = f"{f[-1]} (container {f[0]})"
                    try:
                        machines[name] = {**parse_inventory(name, _run(ssh + [f"pct exec {f[0]} -- sh -s"], 600, INVENTORY_SH)), "ct": f[0]}
                    except Exception as e:
                        problems.append(f"{name}: {e}")
        except Exception as e:
            problems.append(f"{SERVER}: {e}")
    return {"machines": machines, "problems": problems}


# ---------------------------------------------------------------- the lookups
def _client() -> httpx.Client:
    return httpx.Client(timeout=60, headers={"User-Agent": "rosie-security-watch", "Accept": "application/json"}, follow_redirects=True)


def check_packages(c: httpx.Client, packages: list, d: dict) -> list[dict]:
    """OSV.dev for every (where, name, version): one finding per hole and place, with the version it is fixed in."""
    uniq = sorted({(_pep503(n), v) for _, n, v in packages if n and v})
    vulns_of = {}
    for i in range(0, len(uniq), 500):
        chunk = uniq[i:i + 500]
        r = c.post(f"{OSV}/querybatch", json={"queries": [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v} for n, v in chunk]})
        r.raise_for_status()
        for (n, v), res in zip(chunk, r.json().get("results", [])):
            vulns_of[(n, v)] = res.get("vulns") or []
    fetched, out = 0, []
    for where, name, version in packages:
        for ref in vulns_of.get((_pep503(name), version), []):
            vid, mod = ref["id"], ref.get("modified", "")
            cached = d["osv_cache"].get(vid)
            if not cached or cached.get("modified") != mod:
                if fetched >= MAX_OSV_DETAILS:
                    continue                                               # the rest tomorrow night
                r = c.get(f"{OSV}/vulns/{vid}")
                if r.status_code != 200:
                    continue
                fetched += 1
                v = r.json()
                cached = d["osv_cache"][vid] = {"modified": mod, "summary": (v.get("summary") or v.get("details", ""))[:200],
                                                "severity": _severity(v), "aliases": v.get("aliases", [])[:4], "affected": v.get("affected", [])}
            out.append({"key": f"osv:{where}:{_pep503(name)}:{vid}", "kind": "package", "where": where, "name": name, "version": version,
                        "id": vid, "severity": cached["severity"], "summary": cached["summary"],
                        "fix": fixed_version(cached, name, version), "link": f"https://osv.dev/vulnerability/{vid}"})
    return out


def check_apps(c: httpx.Client, apps: dict, d: dict, since: str) -> list[dict]:
    """GitHub security advisories of each app. A known version: every advisory whose range has it. An unknown one (a local build):
    advisories published since the last run."""
    out = []
    for (machine, name), version in apps.items():
        repo = APPS.get(name)
        if not repo:
            continue
        r = c.get(GITHUB.format(repo), params={"per_page": 100, "state": "published"})
        if r.status_code != 200:
            raise RuntimeError(f"GitHub advisories for {repo}: HTTP {r.status_code}")
        for a in r.json():
            ranges = [(x.get("vulnerable_version_range") or "", x.get("patched_versions") or "") for x in a.get("vulnerabilities") or []]
            hit = any(affected(version, rng, fix) for rng, fix in ranges) if version else (a.get("published_at") or "") >= since
            if not hit:
                continue
            fixes = sorted({re.sub(r"^\s*>=?\s*", "", p) for _, p in ranges if p and _version(re.sub(r"^\s*>=?\s*", "", p))}, key=_version)
            out.append({"key": f"ghsa:{machine}:{name}:{a['ghsa_id']}", "kind": "app", "where": machine, "name": name, "version": version,
                        "id": a.get("cve_id") or a["ghsa_id"], "severity": str(a.get("severity") or "unknown").upper(),
                        "summary": (a.get("summary") or "")[:200], "fix": fixes[-1] if fixes else "", "link": a.get("html_url", "")})
    return out


def check_kev(c: httpx.Client, d: dict, since: str, words=()) -> list[dict]:
    """CISA's Known Exploited Vulnerabilities: holes attackers use right now. New entries that name something she watches."""
    r = c.get(KEV)
    r.raise_for_status()
    watch = re.compile(r"\b(" + "|".join(re.escape(w) for w in (*KEV_WATCH, *words)) + r")\b", re.I)
    out = []
    for v in r.json().get("vulnerabilities", []):
        what = f"{v.get('vendorProject', '')} {v.get('product', '')}"
        if v["cveID"] in d["kev_seen"] or (v.get("dateAdded") or "") < since[:10] or not watch.search(what):
            continue
        out.append({"key": f"kev:{v['cveID']}", "kind": "kev", "where": what.strip(), "name": v.get("product", ""), "version": "",
                    "id": v["cveID"], "severity": "EXPLOITED", "summary": f"{v.get('vulnerabilityName', '')}: {v.get('shortDescription', '')}"[:400],
                    "fix": "", "link": f"https://nvd.nist.gov/vuln/detail/{v['cveID']}"})
    return out


def check_nvd(c: httpx.Client, d: dict, since: str, now: dt.datetime, words=()) -> list[dict]:
    """New CVEs that name one of the apps she found (catches holes before an advisory or a fix exists)."""
    end = now.astimezone(dt.timezone.utc)
    out, start = [], max(dt.datetime.fromisoformat(since).astimezone(dt.timezone.utc), end - dt.timedelta(days=119))   # NVD's limit: 120 days
    for i, word in enumerate(words):
        if i:
            time.sleep(NVD_GAP_S)
        r = c.get(NVD, params={"keywordSearch": word, "pubStartDate": start.strftime("%Y-%m-%dT%H:%M:%S.000"),
                               "pubEndDate": end.strftime("%Y-%m-%dT%H:%M:%S.000"), **({"keywordExactMatch": ""} if " " in word else {})})
        if r.status_code != 200:
            raise RuntimeError(f"NVD ({word}): HTTP {r.status_code}")
        for item in r.json().get("vulnerabilities", []):
            cve = item.get("cve", {})
            if cve.get("id") in d["nvd_seen"] or cve.get("vulnStatus") == "Rejected":
                continue
            text = next((x["value"] for x in cve.get("descriptions", []) if x.get("lang") == "en"), "")
            metrics = cve.get("metrics", {})
            sev = next((m[0].get("cvssData", {}).get("baseSeverity") for k in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30")
                        if (m := metrics.get(k))), "") or "UNKNOWN"
            out.append({"key": f"nvd:{cve['id']}", "kind": "nvd", "where": word, "name": word, "version": "", "id": cve["id"],
                        "severity": str(sev).upper(), "summary": text[:400], "fix": "", "link": f"https://nvd.nist.gov/vuln/detail/{cve['id']}"})
    return out


# ---------------------------------------------------------------- patching
def apt_security_cmd(pkgs: list[str]) -> str:
    """Install only these packages' new versions (security fixes); never a removal or a config prompt."""
    bad = [p for p in pkgs if not PKG_NAME.match(p)]
    if bad or not pkgs:
        raise ValueError(f"not a package name: {bad[:3]}" if bad else "nothing to install")
    return ("DEBIAN_FRONTEND=noninteractive apt-get -y -q -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "
            "install --only-upgrade " + " ".join(shlex.quote(p) for p in pkgs) + " </dev/null")


def patch_container(ct: str, pkgs: list[str]) -> str:
    ssh = _ssh_cmd()
    if not ssh or not ct.isdigit():
        raise RuntimeError("no home server is set up (Settings > Security)")
    _run(ssh + [f"pct exec {ct} -- sh -s"], 1800, apt_security_cmd(pkgs) + "\n")
    return f"installed {len(pkgs)} security update{'s' * (len(pkgs) != 1)}"


# Owner-approved patches (Settings > Security > Patch): target -> (button label, what runs on the home server)
OWNER_PATCHES = {
    "server": ("Update the home server", "export DEBIAN_FRONTEND=noninteractive; apt-get update -q && apt-get -y -q "
                "-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold dist-upgrade </dev/null"),
    "pi-hole": ("Update Pi-hole", "{pihole}sh -c 'pihole -up </dev/null'")}


def patch(target: str) -> dict:
    """Start one owner-approved patch (Settings > Security > its button). The result shows in Settings and the Inbox."""
    if target not in OWNER_PATCHES:
        return {"ok": False, "message": f"unknown patch {target!r}"}
    if not _ssh_cmd():
        return {"ok": False, "message": "No home server is set up (Settings > Security)."}
    if not start(_patch, (target,)):
        return {"ok": False, "message": "The security watch is busy; try again in a few minutes."}
    return {"ok": True, "message": OWNER_PATCHES[target][0] + " started. It can take several minutes."}


def _patch(target: str):
    import inbox
    d = load()
    cmd = OWNER_PATCHES[target][1]
    try:
        if "{pihole}" in cmd:
            where = next((m for m in d["machines"].values() if "pi-hole" in m.get("apps", {}) and (m.get("ct") or m is d["machines"].get(SERVER))), None)
            if not where:
                raise RuntimeError("no Pi-hole was found on the home server in the last check")
            cmd = cmd.format(pihole=f"pct exec {where['ct']} -- " if where.get("ct") else "")
        state["doing"] = OWNER_PATCHES[target][0]
        out = _run(_ssh_cmd() + ["sh -s"], 3600, cmd + "\n")
        msg = "Done." + (" A new kernel was installed: restart the home server when it suits you." if "kernel" in out else "")
        result = {"ok": True, "message": msg}
    except Exception as e:
        result = {"ok": False, "message": f"{type(e).__name__}: {str(e)[:300]}"}
    inbox.add("security", f"{OWNER_PATCHES[target][0]}: {'done' if result['ok'] else 'failed'}", result["message"], link="#/settings",
              key=f"sec:owner-patch:{target}:{_iso()}", desktop=False)
    d = load()
    d["patched"] = (d["patched"] + [{"at": _iso(), "where": target, "what": OWNER_PATCHES[target][0], **result}])[-50:]
    save(d)


def self_patch_plan(findings: list[dict]) -> dict:
    """Her own packages to move: {name: (old, new)}; only fixes inside the same major version (a major bump waits for the owner)."""
    plan = {}
    for f in findings:
        if f["kind"] != "package" or f["where"] != "Rosie" or not f["fix"]:
            continue
        old, new = _version(f["version"]), _version(f["fix"])
        if old is None or new is None or new.major != old.major:
            continue
        cur = plan.get(f["name"])
        if not cur or _version(cur[1]) < new:
            plan[f["name"]] = (f["version"], f["fix"])
    return plan


def _healthy(seconds=120) -> bool:
    t0 = time.time()
    time.sleep(5)
    while time.time() - t0 < seconds:
        try:
            if httpx.get(f"http://127.0.0.1:{PORT}/healthz", timeout=3).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(3)
    return False


def _restart_service():
    """Rosie restarts like a slot switch does: the server exits, systemd (Restart=always) starts the live slot again."""
    out = subprocess.run(["bash", "-c", f"ss -ltnp 'sport = :{PORT}' 2>/dev/null | grep -oP 'pid=\\K[0-9]+' | head -1"],
                         capture_output=True, text=True, timeout=10).stdout.strip()
    if out:
        os.kill(int(out), signal.SIGTERM)


def run_self_patch(plan: dict, pip=None, tests=None, restart=None, healthy=None) -> dict:
    """In its own process (systemd-run, so her restart can't stop it): install, test, restart, check; put back on any failure.
    The hooks are for the tests."""
    py = str(ROOT / ".venv" / "bin" / "python")
    pip = pip or (lambda specs: subprocess.run([py, "-m", "pip", "install", "-q", "--disable-pip-version-check", *specs],
                                               capture_output=True, text=True, timeout=1800))
    tests = tests or (lambda: subprocess.run(["nice", "-n", "19", py, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "tests"],
                                             cwd=ROOT, capture_output=True, text=True, timeout=3600))
    restart, healthy = restart or _restart_service, healthy or _healthy
    back = [f"{n}=={old}" for n, (old, _) in plan.items()]
    r = pip([f"{n}=={new}" for n, (_, new) in plan.items()])
    if r.returncode != 0:
        pip(back)
        return {"ok": False, "why": "pip couldn't install the fixed versions: " + (r.stderr or r.stdout).strip()[-300:]}
    t = tests()
    if t.returncode != 0:
        pip(back)
        tail = [ln for ln in (t.stdout or "").splitlines() if ln.strip()][-3:]
        return {"ok": False, "why": "her tests failed with the new versions, so the old ones are back: " + " | ".join(tail)[-300:]}
    restart()
    if healthy():
        return {"ok": True, "why": ""}
    pip(back)
    restart()
    return {"ok": False, "why": "she didn't start again with the new versions, so the old ones are back" + ("" if healthy() else
                                                                                                            " (and she still isn't answering)")}


def _self_patch_main():
    d = load()
    plan = {k: tuple(v) for k, v in (d.get("self_patch") or {}).get("plan", {}).items()}
    result = run_self_patch(plan) if plan else {"ok": False, "why": "nothing to do"}
    d = load()
    d["self_patch"] = {**d.get("self_patch", {}), **result, "done_at": _iso(), "reported": False}
    save(d)


def _start_self_patch(plan: dict, d: dict):
    import selfdev
    d["self_patch"] = {"plan": plan, "started": _iso(), "reported": False}
    save(d)
    selfdev._detached("security-patch", [str(ROOT / ".venv" / "bin" / "python"), str(Path(__file__).resolve()), "self-patch"],
                      ROOT / "data" / "security_patch.log")


# ---------------------------------------------------------------- the Inbox
def _digest(items: list) -> str:
    return hashlib.sha1("|".join(sorted(items)).encode()).hexdigest()[:10]


def _sev_rank(s: str) -> int:
    return {"EXPLOITED": 5, "CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s, 0)


def report(findings: list[dict], patched: list[dict], d: dict, plan: dict | None = None) -> int:
    """Inbox items for what the owner should know; deduplicated by key, so one hole is reported once. Returns how many are new."""
    import inbox
    new = 0
    groups: dict[tuple, list] = {}
    for f in findings:
        groups.setdefault((f["kind"], f["where"], f["name"] if f["kind"] == "app" else f["id"] if f["kind"] in ("kev", "nvd") else ""), []).append(f)
    for (kind, where, name), fs in groups.items():
        fs.sort(key=lambda f: -_sev_rank(f["severity"]))
        worst = fs[0]["severity"].lower()
        if kind == "package":
            if where == "Rosie" and plan and all(f["fix"] and f["name"] in plan and _version(plan[f["name"]][1]) >= _version(f["fix"]) for f in fs):
                continue                                                   # she patches all of these herself; the result is reported
            lines = [f"{f['name']} {f['version']} -> {f['fix'] or 'no fix yet'} ({f['severity'].lower()}: {f['summary'][:80]})" for f in fs[:6]]
            title = f"{len(fs)} known security hole{'s' * (len(fs) != 1)} in Python packages: {where}"
            body = "; ".join(lines) + ("; ..." if len(fs) > 6 else "") + ". Fix: update those packages in that app's environment and test it."
        elif kind == "app":
            fix = max((f["fix"] for f in fs if f["fix"]), key=_version, default="")
            title = f"{name} {fs[0]['version'] or ''} on {where}: {len(fs)} known security hole{'s' * (len(fs) != 1)} (worst: {worst})"
            body = (f"Fixed in {fix} or newer. " if fix else "") + STEPS.get(name, "") + " " + \
                   "; ".join(f"{f['id']}: {f['summary'][:90]}" for f in fs[:4])
        elif kind == "kev":
            f = fs[0]
            title = f"Being exploited now: {f['id']} in {where}"
            body = f"{f['summary']} Check whether you use {where}; if so, update it today. {f['link']}"
        else:
            f = fs[0]
            title = f"New security hole reported for {where}: {f['id']} ({worst})"
            body = f"{f['summary']} {f['link']}"
        if inbox.add("security", title, body, link="#/settings", key=f"sec:{kind}:{where}:{name}:{_digest([f['key'] for f in fs])}"):
            new += 1
    for p in patched:
        if inbox.add("security", f"Security updates installed: {p['where']}", p["what"], link="#/settings",
                     key=f"sec:patched:{p['where']}:{p['at']}", desktop=False):
            new += 1
    waiting = {name: _digest(m["security"]) for name, m in d["machines"].items() if not m.get("ct") and m.get("security")}
    before, d["os_waiting"] = d.get("os_waiting", {}), waiting
    for name, m in d["machines"].items():
        if name in waiting and before.get(name) == waiting[name]:           # still waiting a day later: unattended-upgrades didn't
            fix = "Run sudo apt upgrade." if name == "this PC" else "Press Update the home server in Rosie's Settings > Security."
            if inbox.add("security", f"{len(m['security'])} security updates waiting on {name}", ", ".join(m["security"][:12]) + ". " + fix,
                         link="#/settings", key=f"sec:os:{name}:{_digest(m['security'])}"):
                new += 1
    return new


# ---------------------------------------------------------------- one run
def check(now: dt.datetime | None = None, client: httpx.Client | None = None) -> dict:
    """The whole nightly check. Returns a summary (also saved)."""
    now = now or _now()
    d = load()
    since = d.get("ran_at") or _iso(now - dt.timedelta(days=FIRST_LOOKBACK_D))
    state["doing"] = "Taking stock of your PC and home server"
    inv = inventory()
    d["machines"] = {k: {kk: vv for kk, vv in m.items() if kk != "packages"} for k, m in inv["machines"].items()}
    problems = list(inv["problems"])
    packages = [p for m in inv["machines"].values() for p in m["packages"]]
    apps = {(k, name): v for k, m in inv["machines"].items() for name, v in m["apps"].items()}
    words = watch_words({name for _, name in apps})
    findings = []
    with (client or _client()) as c:
        for label, fn in (("Python packages", lambda: check_packages(c, packages, d)), ("app advisories", lambda: check_apps(c, apps, d, since)),
                          ("exploited-now list", lambda: check_kev(c, d, since, words)), ("new CVEs", lambda: check_nvd(c, d, since, now, words))):
            state["doing"] = f"Looking up {label}"
            try:
                findings += fn()
            except Exception as e:
                problems.append(f"{label}: {type(e).__name__}: {str(e)[:160]}")
    patched, auto = [], config.load().get("security_autopatch", True)
    for name, m in inv["machines"].items():
        if auto and m.get("ct") and m["security"]:
            state["doing"] = f"Installing security updates on {name}"
            try:
                patched.append({"at": _iso(), "where": name, "what": patch_container(m["ct"], m["security"]) + ": " + ", ".join(m["security"][:20])})
                d["machines"][name]["security"] = []
            except Exception as e:
                problems.append(f"patching {name}: {type(e).__name__}: {str(e)[:160]}")
    d["kev_seen"] = (d["kev_seen"] + [f["id"] for f in findings if f["kind"] == "kev"])[-2000:]
    d["nvd_seen"] = (d["nvd_seen"] + [f["id"] for f in findings if f["kind"] == "nvd"])[-2000:]
    d["findings"] = {f["key"]: f for f in findings if f["kind"] in ("package", "app")}
    d["patched"] = (d["patched"] + patched)[-50:]
    d.update(ran_at=_iso(now), ran_on=now.date().isoformat(), problems=problems,
             counted={"packages": len(packages), "apps": len(apps), "machines": len(inv["machines"])})
    d["osv_cache"] = {k: v for k, v in d["osv_cache"].items() if any(f["id"] == k for f in findings)}   # only what's still relevant
    plan = self_patch_plan(findings) if auto and LINUX else {}
    last = d.get("self_patch") or {}
    if plan and last.get("done_at") and not last.get("ok") and {k: tuple(v) for k, v in last.get("plan", {}).items()} == plan:
        plan = {}                                                          # the same patch failed before: the owner gets the Inbox item
    report(findings, patched, d, plan)
    save(d)
    if plan:
        state["doing"] = "Patching her own packages (in the background)"
        _start_self_patch(plan, d)
    return status()


def _report_self_patch():
    """Tell the owner how her own patch went (it finished in its own process, maybe after a restart)."""
    d = load()
    sp = d.get("self_patch") or {}
    if not sp.get("done_at") or sp.get("reported"):
        return
    import inbox
    names = ", ".join(f"{n} {o} -> {v}" for n, (o, v) in sp.get("plan", {}).items())
    if sp.get("ok"):
        inbox.add("security", "Rosie patched herself", f"Security fixes installed and tested: {names}.", link="#/settings",
                  key=f"sec:self:{sp['done_at']}", desktop=False)
    else:
        inbox.add("security", "Rosie couldn't patch herself", f"{names}: {sp.get('why', '')} A Claude session can do it with you.",
                  link="#/settings", key=f"sec:self:{sp['done_at']}")
    sp["reported"] = True
    d["self_patch"] = sp
    save(d)


# ---------------------------------------------------------------- the job (automations.py) and Settings
def due(d: dict, now: dt.datetime) -> str | None:
    """None when tonight's check may start; otherwise why not."""
    if not RUN_FROM <= now.time() <= RUN_TO:
        return "outside the night window"
    if d.get("ran_on") == now.date().isoformat():
        return "already checked today"
    if corner.owner_recent(AWAY_S):
        return "the owner was here recently"
    try:
        if time.time() - corner.STAGE_FLAG.stat().st_mtime < 3600:
            return "a stage check is running"                              # its tests use the same Python packages
    except OSError:
        pass
    return None


def start(target=None, args=()) -> bool:
    """Run `target` (default: the whole check) in a thread, never two at once."""
    with _lock:
        if state["running"]:
            return False
        state["running"] = True

    def guarded():
        try:
            (target or check)(*args)
            state["last_error"] = ""
        except Exception as e:
            state["last_error"] = f"{type(e).__name__}: {str(e)[:160]}"
            d = load()
            d["ran_on"] = _now().date().isoformat()                        # a failing check isn't retried every minute
            save(d)
        finally:
            state.update(running=False, doing="")
    threading.Thread(target=guarded, daemon=True, name="security-watch").start()
    return True


def job(now: dt.datetime, cfg: dict):
    """Called every automations tick."""
    if testguard.active():
        return
    _report_self_patch()
    if cfg.get("security_watch", True) and not state["running"] and due(load(), now) is None:
        start()


def status() -> dict:
    d = load()
    cfg = config.load()
    fs = list(d["findings"].values())
    sp = d.get("self_patch") or {}
    return {"on": bool(cfg.get("security_watch", True)), "autopatch": bool(cfg.get("security_autopatch", True)),
            "server": bool(cfg.get("proxmox_ssh")), "doing": state["doing"], "running": state["running"], "last_error": state["last_error"],
            "ran_at": d.get("ran_at"), "counted": d.get("counted", {}), "problems": d.get("problems", [])[:6],
            "holes": len(fs), "serious": sum(_sev_rank(f["severity"]) >= 3 for f in fs),
            "apps": sorted({f"{f['name']} on {f['where']}" for f in fs if f["kind"] == "app"}),
            "waiting": {k: len(m.get("security", [])) for k, m in d["machines"].items() if m.get("security")},
            "patched": d["patched"][-5:], "self_patch": {k: sp.get(k) for k in ("started", "done_at", "ok", "why")} if sp else None,
            "found": watch_words({a for m in d["machines"].values() for a in m.get("apps", {})}),
            "ssh": cfg.get("proxmox_ssh", ""), "ssh_key": cfg.get("proxmox_ssh_key", ""),
            "patches": [{"target": k, "label": v[0]} for k, v in OWNER_PATCHES.items()
                        if k == "server" or any("pi-hole" in m.get("apps", {}) for m in d["machines"].values() if m.get("ct") or m is d["machines"].get(SERVER))]}


SSH_ADDR = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$")


def set_server(ssh: str, key: str) -> dict:
    """Settings > Security > Home server: user@address for SSH (key login only), and the key file (under ~/.ssh)."""
    ssh, key = ssh.strip(), key.strip()
    if ssh and not SSH_ADDR.match(ssh):
        return {"ok": False, "message": "Use user@address, like root@192.168.1.10."}
    if key and not (key.startswith("~/.ssh/") and ".." not in key and re.fullmatch(r"[\w~./-]+", key)):
        return {"ok": False, "message": "The key has to be a file in ~/.ssh, like ~/.ssh/id_ed25519."}
    cfg = config.load()
    cfg.update(proxmox_ssh=ssh, proxmox_ssh_key=key)
    config.save(cfg)
    return {"ok": True, "message": "Saved. The next check includes it." if ssh else "Saved. Only this PC is checked now."}


if __name__ == "__main__":
    if sys.argv[1:] == ["self-patch"]:
        _self_patch_main()
    elif sys.argv[1:] == ["check"]:
        print(json.dumps(check(), indent=1))
