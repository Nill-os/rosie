r"""Windows and Linux differences in one place: the shell, opening things, desktop pop-ups and Tailscale (2026-10-14 for the Windows
copy; in the main code since 2026-10-23, owner: "i want ota updates so when i update rosie hers also updates"). On Windows Rosie runs
from %LOCALAPPDATA%\Rosie (tools/windows/launcher.py); code that needs an OS tool asks here first."""
import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"
ROOT = Path(__file__).resolve().parent.parent
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)     # a helper program never flashes a console window


def pick(linux, windows):
    """One text or value per system, so a message names the command that exists on this PC."""
    return windows if IS_WIN else linux


def google_login(account: str | None = None, cap: bool = False) -> str:
    """What to do to sign in to Google, as a phrase ("run ~/assistant/login.sh"). login.sh and login-gmail.sh are bash scripts, so Windows has
    a shortcut that does both (tools/windows/Rosie Google Sign-in.bat). account: the Gmail account name; None means Calendar."""
    text = pick("run " + ("~/assistant/login.sh" if account is None else f"~/assistant/login-gmail.sh {account}"),
                "open Rosie Google Sign-in (the shortcut on your desktop)")
    return text[0].upper() + text[1:] if cap else text


def ps_encode(script: str) -> str:
    """PowerShell -EncodedCommand text: no quoting problems, whatever the script contains."""
    return base64.b64encode(script.encode("utf-16-le")).decode()


PS_PRELUDE = "$ProgressPreference='SilentlyContinue'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; "


def shell_argv(command: str) -> list[str]:
    """Run one command line in the user's shell (PowerShell on Windows, bash here)."""
    if IS_WIN:
        return ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand",
                ps_encode(PS_PRELUDE + command + "\nif (-not $?) { if ($LASTEXITCODE) { exit $LASTEXITCODE } else { exit 1 } }")]   # the real exit code
    return ["bash", "-lc", command]


def run(argv, **kw):
    """subprocess.run that never opens a console window on Windows and never crashes on odd output bytes."""
    if kw.get("text") or kw.get("universal_newlines"):
        kw.setdefault("encoding", "utf-8")
        kw.setdefault("errors", "replace")
    if IS_WIN:
        kw["creationflags"] = kw.get("creationflags", 0) | NO_WINDOW
    return subprocess.run(argv, **kw)


def open_target(target: str) -> dict:
    """Open a file, folder, URL or app on the user's screen (like double-clicking it)."""
    if IS_WIN:
        try:
            os.startfile(target)                                   # noqa: S606  (Windows only; the user asked to open it)
        except OSError as e:
            return {"error": f"Windows could not open it: {e}"}
        return {"status": "launched; check your screen. I cannot see the screen to confirm a window appeared.", "target": target}
    try:
        p = run(["xdg-open", target], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=8)
    except subprocess.TimeoutExpired:
        return {"status": "launched (still starting)", "target": target}
    except FileNotFoundError:
        return {"error": "No desktop opener (xdg-open) on this computer."}
    if p.returncode != 0:
        return {"error": f"xdg-open failed (code {p.returncode}): {(p.stderr or p.stdout).strip()[:200]}"}
    return {"status": "launched; check your screen. I cannot see the screen to confirm a window appeared.", "target": target}


_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$t = [Security.SecurityElement]::Escape($env:ROSIE_TOAST_TITLE); $b = [Security.SecurityElement]::Escape($env:ROSIE_TOAST_BODY)
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$b</text></binding></visual></toast>")
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show((New-Object Windows.UI.Notifications.ToastNotification $x))
"""


def notify(title: str, body: str = "") -> bool:
    """A desktop pop-up. Windows: a toast in the Action Center (shown under 'Windows PowerShell'). Returns False if it could not be shown."""
    try:
        if IS_WIN:
            env = {**os.environ, "ROSIE_TOAST_TITLE": title[:120], "ROSIE_TOAST_BODY": body[:300]}
            r = run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", ps_encode(_TOAST)],
                    env=env, capture_output=True, timeout=15)
            return r.returncode == 0
        if not shutil.which("notify-send"):
            return False
        r = run(["notify-send", "-a", "Rosie", "-u", "normal", "-t", "20000", title[:120], body[:300]], capture_output=True, timeout=6)
        return r.returncode == 0
    except Exception:
        return False


def tailscale_bin() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    if IS_WIN:
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
            p = Path(base) / "Tailscale" / "tailscale.exe"
            if base and p.exists():
                return str(p)
    return None


def agent_path() -> str:
    """PATH for finding and running AI coding agents (claude, codex, ...), including where their installers put them."""
    home = Path.home()
    extra = [home / ".local" / "bin", home / ".opencode" / "bin", home / "go" / "bin"]
    if IS_WIN:
        appdata = os.environ.get("APPDATA", str(home / "AppData" / "Roaming"))
        local = os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))
        extra += [Path(appdata) / "npm", Path(local) / "Programs" / "claude", home / ".bun" / "bin"]
    else:
        extra += [Path("/usr/local/bin"), Path("/usr/bin"), Path("/bin")]
    parts = [str(p) for p in extra] + os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(dict.fromkeys(p for p in parts if p))


def python_cmd() -> str:
    """This Rosie's own Python (for the MCP server line shown in Settings)."""
    return sys.executable
