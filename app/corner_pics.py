"""Pictures for Rosie's corner (owner 2026-10-14: "allow her to see pictures also and save them for later but run them through a virus tool").

Every picture goes through these checks, in this order, before it is saved or shown. Any failure throws it away and logs it as blocked:
  1. type: only JPEG, PNG, GIF or WebP, judged by the file's first bytes (never its name or the server's word);
  2. size: at most 10 MB and 40 megapixels (read from the file header, before anything decodes it);
  3. virus scan: ClamAV (`clamscan`); anything flagged, or any scanner error, is deleted;
  4. clean copy: Pillow opens it and saves a fresh PNG or JPEG with no metadata. Only that copy is kept, in data/corner/images/.

Fails closed: without `clamscan` or without Pillow no picture is saved at all."""
import hashlib
import importlib
import importlib.util
import os
import re
import secrets
import shutil
import struct
import subprocess

import corner
import testguard

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000
MIN_SIDE = 64
SCAN_TIMEOUT = 600
FORMATS = ("jpeg", "png", "gif", "webp")
NO_SCANNER = "Pictures are off until the virus scanner is installed."
NO_PILLOW = "Pictures are off until the picture cleaner (Pillow) is installed."


def scanner() -> str | None:
    return shutil.which("clamscan")


def _pil():
    """Pillow, imported only when a picture is cleaned (it is not in every Rosie install). None when missing."""
    if importlib.util.find_spec("PIL") is None:
        return None
    try:
        return importlib.import_module("PIL.Image")
    except Exception:
        return None


def ready() -> tuple[bool, str]:
    if not scanner():
        return False, NO_SCANNER
    if importlib.util.find_spec("PIL") is None:
        return False, NO_PILLOW
    return True, "Pictures are on: each one is virus-checked and cleaned before it is kept."


# ---------------------------------------------------------------- 1. type by the file's own bytes
def sniff(b: bytes) -> str | None:
    if b[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    return None


# ---------------------------------------------------------------- 2. size from the header (no decoder touches it yet)
def dimensions(b: bytes, kind: str) -> tuple[int, int] | None:
    try:
        if kind == "png":
            if b[12:16] != b"IHDR":
                return None
            return struct.unpack(">II", b[16:24])
        if kind == "gif":
            return struct.unpack("<HH", b[6:10])
        if kind == "webp":
            chunk = b[12:16]
            if chunk == b"VP8X":
                w = int.from_bytes(b[24:27], "little") + 1
                h = int.from_bytes(b[27:30], "little") + 1
                return w, h
            if chunk == b"VP8 ":
                if b[23:26] != b"\x9d\x01\x2a":
                    return None
                w, h = struct.unpack("<HH", b[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if chunk == b"VP8L":
                if b[20] != 0x2F:
                    return None
                bits = int.from_bytes(b[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            return None
        if kind == "jpeg":
            i, n = 2, len(b)
            while i + 9 < n:
                if b[i] != 0xFF:
                    return None
                marker = b[i + 1]
                if marker == 0xFF:
                    i += 1
                    continue
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg = struct.unpack(">H", b[i + 2:i + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", b[i + 5:i + 9])
                    return w, h
                if marker == 0xDA or seg < 2:
                    return None
                i += 2 + seg
            return None
    except (struct.error, IndexError):
        return None
    return None


def precheck(b: bytes) -> tuple[str | None, str, tuple[int, int] | None]:
    """Checks 1 and 2. Returns (kind, problem, (w, h)); problem is '' when the picture may go on to the virus scan."""
    kind = sniff(b)
    if not kind:
        return None, "not a real picture (JPEG, PNG, GIF or WebP)", None
    if len(b) > MAX_BYTES:
        return kind, "bigger than 10 MB", None
    wh = dimensions(b, kind)
    if not wh or wh[0] <= 0 or wh[1] <= 0:
        return kind, "its size could not be read", None
    if wh[0] * wh[1] > MAX_PIXELS:
        return kind, "more than 40 megapixels", wh
    if min(wh) < MIN_SIDE:
        return kind, "too small to be worth keeping", wh
    return kind, "", wh


# ---------------------------------------------------------------- 3. ClamAV
def scan(paths: list) -> dict:
    """Scan files with clamscan in one run (it loads its virus database once). Returns {path: 'ok' | 'flagged: …' | 'error: …'}.
    Anything clamscan doesn't explicitly call OK counts as not OK."""
    out = {str(p): "error: not scanned" for p in paths}
    exe = scanner()
    if not exe:
        return {p: "error: no virus scanner" for p in out}
    if not paths:
        return {}
    cmd = ["nice", "-n", "19", exe, "--no-summary", "--stdout", "--"] + [str(p) for p in paths]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=SCAN_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return {p: f"error: {type(e).__name__}" for p in out}
    for line in r.stdout.splitlines():
        m = re.match(r"^(.*): (.*)$", line.strip())
        if not m or m.group(1) not in out:
            continue
        verdict = m.group(2).strip()
        if verdict == "OK":
            out[m.group(1)] = "ok"
        elif verdict.endswith("FOUND"):
            out[m.group(1)] = "flagged: " + verdict[:-5].strip()[:120]
        else:
            out[m.group(1)] = "error: " + verdict[:120]
    if r.returncode not in (0, 1):                  # 2 = the scanner itself had trouble: trust nothing from this run
        return {p: (v if v.startswith("flagged") else f"error: scanner exit {r.returncode}") for p, v in out.items()}
    return out


# ---------------------------------------------------------------- 4. clean copy with Pillow
def reencode(src, kind: str, wh: tuple[int, int]) -> tuple[bytes, str, tuple[int, int]]:
    """Open the scanned file, check it is what its header said, and draw a brand-new picture from its pixels (no metadata, no extra
    chunks, first frame only). Returns (bytes, 'png' | 'jpg', (w, h)). Raises on anything odd."""
    import io
    Image = _pil()
    if Image is None:
        raise RuntimeError(NO_PILLOW)
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    want = {"jpeg": "JPEG", "png": "PNG", "gif": "GIF", "webp": "WEBP"}[kind]
    with Image.open(src) as im:
        if im.format != want:
            raise ValueError(f"the file says {kind} but opens as {im.format}")
        im.verify()
    with Image.open(src) as im:
        if tuple(im.size) != tuple(wh):
            raise ValueError("its size changed between checks")
        im.seek(0)
        im.load()
        alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        mode = "RGBA" if alpha else "RGB"
        px = im.convert(mode)
        fresh = Image.new(mode, px.size)
        fresh.paste(px)
    buf = io.BytesIO()
    if alpha or kind in ("png", "gif"):
        fresh.save(buf, format="PNG", optimize=True)
        ext = "png"
    else:
        fresh.save(buf, format="JPEG", quality=88, optimize=True)
        ext = "jpg"
    return buf.getvalue(), ext, fresh.size


THUMB_SIDE = 320


def thumbnail(b: bytes) -> tuple[bytes | None, str]:
    """A small preview for the corner's Live view: checks 1 and 2 (type by bytes, size from the header), then Pillow draws a fresh
    JPEG of at most 320 px from the pixels. It stays in memory and is served only from Rosie; the remote picture never reaches the
    owner's browser. Returns (jpeg, '') or (None, why it is blocked). Not a kept picture: those still go through ClamAV."""
    import io
    kind, problem, wh = precheck(b or b"")
    if problem:
        return None, problem
    Image = _pil()
    if Image is None:
        return None, NO_PILLOW
    try:
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        want = {"jpeg": "JPEG", "png": "PNG", "gif": "GIF", "webp": "WEBP"}[kind]
        with Image.open(io.BytesIO(b)) as im:
            if im.format != want or tuple(im.size) != tuple(wh):
                return None, "it is not what its header says"
            im.verify()
        with Image.open(io.BytesIO(b)) as im:
            im.seek(0)
            im.load()
            px = im.convert("RGB")
        px.thumbnail((THUMB_SIDE, THUMB_SIDE))
        fresh = Image.new("RGB", px.size)
        fresh.paste(px)
        buf = io.BytesIO()
        fresh.save(buf, format="JPEG", quality=75)
        return buf.getvalue(), ""
    except Exception as e:
        return None, f"could not be read ({type(e).__name__})"


def check_and_keep(items: list[dict]) -> list[dict]:
    """Run downloaded pictures through all four checks and keep the clean copies.

    items: [{"bytes": b, "src_url", "page_url", "page_title", "alt"}]. Returns one result per item: {"ok": True, "id": picture id} or
    {"ok": False, "why": reason}. Every blocked picture is written to the trail."""
    ok, why = ready()
    if not ok:
        for it in items:
            corner.log("picture", it.get("src_url", ""), it.get("page_title", ""), "blocked", why)
        return [{"ok": False, "why": why} for _ in items]
    results: list[dict | None] = [None] * len(items)
    staged = []                                               # (index, path, kind, wh)
    tmp = corner.tmp_dir()
    testguard.check(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    for i, it in enumerate(items):
        kind, problem, wh = precheck(it.get("bytes") or b"")
        if problem:
            results[i] = {"ok": False, "why": problem}
            continue
        p = tmp / (secrets.token_hex(16) + ".bin")            # never the picture's own name or extension
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(it["bytes"])
        staged.append((i, p, kind, wh))
    try:
        verdicts = scan([p for _, p, _, _ in staged]) if staged else {}
        for i, p, kind, wh in staged:
            v = verdicts.get(str(p), "error: not scanned")
            if v != "ok":
                results[i] = {"ok": False, "why": "virus scanner " + ("flagged it (" + v[9:] + ")" if v.startswith("flagged") else "could not check it (" + v[7:] + ")")}
                continue
            try:
                data, ext, size = reencode(p, kind, wh)
            except Exception as e:
                results[i] = {"ok": False, "why": f"could not be cleaned ({type(e).__name__})"}
                continue
            results[i] = {"ok": True, "data": data, "ext": ext, "size": size}
    finally:
        for _, p, _, _ in staged:                             # the downloaded originals never stay
            p.unlink(missing_ok=True)
    out = []
    for it, r in zip(items, results):
        if r["ok"] and corner.disk_low():                     # disk safety: never fill the disk (on top of the storage budget)
            r = {"ok": False, "why": "less than 5 GB free on the disk"}
        if not r["ok"]:
            corner.log("picture", it.get("src_url", ""), it.get("page_title", ""), "blocked", r["why"])
            out.append(r)
            continue
        out.append({"ok": True, "id": _store(it, r["data"], r["ext"], r["size"])})
    corner.enforce_storage()                                  # nonstop too (owner 2026-10-22: "10 gb for pictures")
    return out


def _store(it: dict, data: bytes, ext: str, size: tuple[int, int]) -> int:
    sha = hashlib.sha256(data).hexdigest()
    with corner._db() as db:
        old = db.execute("SELECT id FROM pictures WHERE sha256=?", (sha,)).fetchone()
    if old:                                                   # the same picture again (another page used it): keep one copy
        return old["id"]
    d = corner.images_dir()
    testguard.check(d)
    d.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(16) + "." + ext
    fd = os.open(d / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    with corner._db() as db:
        cur = db.execute("INSERT INTO pictures(at, file, fmt, width, height, bytes, src_url, page_url, page_title, alt, sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (corner._iso(), name, ext, size[0], size[1], len(data), str(it.get("src_url", ""))[:2000], str(it.get("page_url", ""))[:2000],
                          str(it.get("page_title", ""))[:300], str(it.get("alt", ""))[:300], sha))
        pid = cur.lastrowid
    corner.log("picture", it.get("src_url", ""), it.get("page_title", ""), "saved", f"kept a clean {ext.upper()} copy ({size[0]}×{size[1]})")
    return pid


CONTENT_TYPES = {"png": "image/png", "jpg": "image/jpeg"}
