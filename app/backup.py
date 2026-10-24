"""Encrypted backups of everything that cannot be re-downloaded: databases (chats, tasks, memory, mail state, projects), settings, the notes vault and self-built tools.
Not included on purpose: login tokens, the access code and session key (you can sign in again), speech models (re-download), logs.
Each backup is checked right after it is made: it is decrypted again and every database is integrity-checked, so a backup can never silently be unreadable.
Restore never overwrites live data: it unpacks into a NEW folder and you copy back what you want.

  python app/backup.py backup                 make + verify a backup, keep the newest 14 locally
  python app/backup.py verify FILE            decrypt and check a backup
  python app/backup.py restore FILE FOLDER    unpack into a new folder
  python app/backup.py status                 age and result of the latest backup (JSON)
"""
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUPS = Path(os.environ.get("ROSIE_BACKUP_DIR") or Path.home() / "backups" / "rosie")
CONF = Path(os.environ.get("ROSIE_BACKUP_CONF") or Path.home() / ".config" / "rosie")
KEEP_FILES = ("config.json", "voice_chat.json")
KEEP_SECRETS = ("google_client.json",)             # downloadable but annoying to recreate; tokens and codes are NOT backed up
KEEP = 14


def _paths():
    return ROOT / "data", ROOT / "vault", ROOT / "secrets"


def passphrase_file() -> Path:
    """The passphrase that locks the backups. Created once. COPY IT OFF THIS COMPUTER (password manager): without it a dead disk means no restore."""
    p = CONF / "backup.pass"
    if not p.exists():
        CONF.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # private from the first byte
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(36) + "\n")
    return p


def _gpg(*args, passfile: Path):
    home = CONF / "gnupg"
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    cmd = ["gpg", "--homedir", str(home), "--batch", "--yes", "--quiet", "--pinentry-mode", "loopback", "--passphrase-file", str(passfile), *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    finally:                                                 # gpg starts an agent for this home; it is not needed afterwards
        subprocess.run(["gpgconf", "--homedir", str(home), "--kill", "gpg-agent"], capture_output=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError("gpg failed: " + (r.stderr or r.stdout).strip()[-200:])


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _db_counts(p: Path) -> dict:
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        out = {}
        for (t,) in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"):
            out[t] = con.execute(f'select count(*) from "{t}"').fetchone()[0]
        return out
    finally:
        con.close()


def make_snapshot(dest: Path, passfile: Path | None = None, data: Path | None = None, vault: Path | None = None, sec: Path | None = None) -> Path:
    d0, v0, s0 = _paths()
    data, vault, sec = data or d0, vault or v0, sec or s0
    passfile = passfile or passphrase_file()
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rosie-bk-") as tmp:
        stage = Path(tmp) / "stage"
        (stage / "data").mkdir(parents=True)
        nested = [p for p in (data / "corner" / "corner.db",) if p.exists()]   # her corner's finds, groups and notes (audit 2026-10-20)
        for db in sorted(data.glob("*.db")) + nested:            # the sqlite backup API: a consistent copy even while the app is writing
            src = sqlite3.connect(db)
            (stage / "data" / db.relative_to(data)).parent.mkdir(parents=True, exist_ok=True)
            dst = sqlite3.connect(stage / "data" / db.relative_to(data))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
        for name in KEEP_FILES:
            if (data / name).exists():
                shutil.copy2(data / name, stage / "data" / name)
        if (data / "user_tools").is_dir():
            shutil.copytree(data / "user_tools", stage / "data" / "user_tools", ignore=shutil.ignore_patterns("site", "__pycache__"))
        if vault.is_dir():
            shutil.copytree(vault, stage / "vault")
        (stage / "secrets").mkdir()
        for name in KEEP_SECRETS:
            if (sec / name).exists():
                shutil.copy2(sec / name, stage / "secrets" / name)
        files = {}
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                files[str(p.relative_to(stage))] = {"sha256": _sha(p), "bytes": p.stat().st_size}
        dbs = {k: _db_counts(stage / k) for k in files if k.endswith(".db")}
        (stage / "MANIFEST.json").write_text(json.dumps({"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": files, "databases": dbs}, indent=1))
        tar = Path(tmp) / "snap.tar.gz"
        with tarfile.open(tar, "w:gz") as t:
            t.add(stage, arcname=".")
        out = dest / f"rosie-data-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz.gpg"
        _gpg("--symmetric", "--cipher-algo", "AES256", "-o", str(out), str(tar), passfile=passfile)
    return out


def _unpack(file: Path, passfile: Path, target: Path):
    with tempfile.TemporaryDirectory(prefix="rosie-bk-") as tmp:
        tar = Path(tmp) / "snap.tar.gz"
        _gpg("-d", "-o", str(tar), str(file), passfile=passfile)
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tar, "r:gz") as t:
            t.extractall(target, filter="data")


def verify(file: Path, passfile: Path | None = None) -> dict:
    """Decrypt, unpack, check every file hash and every database. Raises or returns {'ok': False,...} on any problem."""
    passfile = passfile or passphrase_file()
    problems = []
    with tempfile.TemporaryDirectory(prefix="rosie-vf-") as tmp:
        try:
            _unpack(file, passfile, Path(tmp))
        except Exception as e:
            return {"ok": False, "problems": [f"cannot decrypt/unpack: {e}"]}
        man = json.loads((Path(tmp) / "MANIFEST.json").read_text())
        for rel, info in man["files"].items():
            p = Path(tmp) / rel
            if not p.exists():
                problems.append(f"missing {rel}")
            elif _sha(p) != info["sha256"]:
                problems.append(f"changed {rel}")
        for rel, counts in man["databases"].items():
            con = sqlite3.connect(Path(tmp) / rel)
            try:
                if con.execute("pragma integrity_check").fetchone()[0] != "ok":
                    problems.append(f"damaged database {rel}")
            finally:
                con.close()
            if _db_counts(Path(tmp) / rel) != counts:
                problems.append(f"row counts differ in {rel}")
    return {"ok": not problems, "files": len(man["files"]), "databases": len(man["databases"]), "created": man["created"], "problems": problems}


def restore(file: Path, target: Path, passfile: Path | None = None) -> Path:
    target = Path(target)
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f"{target} is not empty: restore only unpacks into a new, empty folder so nothing live is overwritten.")
    _unpack(file, passfile or passphrase_file(), target)
    return target


def rotate(dest: Path, keep: int = KEEP, days: int = 30):
    """Keep the newest `keep`, and also the newest one of each of the last `days` days: a switch makes a backup each time, so
    counting alone kept barely a day of history (audit 2026-10-20)."""
    import time
    files = sorted(dest.glob("rosie-data-*.gpg"), reverse=True)
    keepers, days_seen = set(files[:keep]), set()
    for f in files:
        day = time.strftime("%Y-%m-%d", time.localtime(f.stat().st_mtime))
        if day not in days_seen and time.time() - f.stat().st_mtime < days * 86400:
            days_seen.add(day)
            keepers.add(f)
    for old in files:
        if old not in keepers:
            old.unlink()


def status() -> dict:
    files = sorted(BACKUPS.glob("rosie-data-*.gpg"), reverse=True)
    if not files:
        return {"exists": False}
    last = BACKUPS / "last_verify.json"
    v = json.loads(last.read_text()) if last.exists() else {}
    return {"exists": True, "file": files[0].name, "age_hours": round((time.time() - files[0].stat().st_mtime) / 3600, 1), "count": len(files), "verified": v.get("ok"), "problems": v.get("problems", [])}


def run_backup() -> Path:
    out = make_snapshot(BACKUPS)
    v = verify(out)
    (BACKUPS / "last_verify.json").write_text(json.dumps({**v, "file": out.name}))
    if not v["ok"]:                                           # set aside, so rotation never keeps it in place of a good one
        out.rename(out.with_name(out.name + ".failed"))
        raise SystemExit("Backup was made but did NOT pass its check: " + "; ".join(v["problems"]))
    rotate(BACKUPS)
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "backup":
        print(run_backup())
    elif cmd == "verify" and len(sys.argv) > 2:
        r = verify(Path(sys.argv[2]))
        print(json.dumps(r, indent=1))
        sys.exit(0 if r["ok"] else 1)
    elif cmd == "restore" and len(sys.argv) > 3:
        print("Restored into", restore(Path(sys.argv[2]), Path(sys.argv[3])))
    elif cmd == "status":
        print(json.dumps(status()))
    else:
        print(__doc__)
