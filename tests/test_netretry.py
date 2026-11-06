import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(tmp_path, body, fake_git_fails):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    counter = tmp_path / "count"
    counter.write_text("0")
    git = bindir / "git"
    git.write_text(f'#!/bin/sh\nn=$(cat "{counter}"); n=$((n+1)); echo $n > "{counter}"\n[ $n -le {fake_git_fails} ] && exit 1\nexit 0\n')
    git.chmod(0o755)
    getent = bindir / "getent"
    getent.write_text("#!/bin/sh\nexit 0\n")
    getent.chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", ROSIE_NET_PAUSE="0", ROSIE_NET_STEP="0", ROSIE_NET_WAIT="0")
    r = subprocess.run(["bash", "-c", f'. "{ROOT}/netretry.sh"; {body}'], env=env, capture_output=True, text=True)
    return r.returncode, int(counter.read_text())


def test_push_is_retried_until_it_works(tmp_path):
    assert _run(tmp_path, "net_retry git push", 2) == (0, 3)


def test_push_gives_up_after_three_tries(tmp_path):
    assert _run(tmp_path, "net_retry git push", 5) == (1, 3)


def test_net_wait_times_out_when_the_host_never_resolves(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    g = bindir / "getent"
    g.write_text("#!/bin/sh\nexit 2\n")
    g.chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", ROSIE_NET_WAIT="0", ROSIE_NET_STEP="1")
    r = subprocess.run(["bash", "-c", f'. "{ROOT}/netretry.sh"; net_wait'], env=env)
    assert r.returncode == 1


def test_both_scripts_use_the_retry_helpers():
    for name in ("backup-data.sh", "sync-repo.sh"):
        text = (ROOT / name).read_text()
        assert "netretry.sh" in text and "net_retry git push" in text or "net_retry sh -c" in text
        assert "net_wait" in text
