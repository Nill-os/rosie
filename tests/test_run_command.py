# board #886 quirk-windows-pipe-held-open
import sys
import time

import pytest

import pc_tools


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(pc_tools, "_log", lambda *a, **k: None)


def test_exit_codes_and_text_come_back(tmp_path):
    assert pc_tools.run_command("exit 3", str(tmp_path), 10)["exit_code"] == 3
    assert "héllo" in pc_tools.run_command("echo héllo", str(tmp_path), 10)["stdout"]
    assert pc_tools.run_command("echo ok", str(tmp_path), "not a number")["exit_code"] == 0
    assert "Could not run it" in pc_tools.run_command("echo x", str(tmp_path / "missing"), 5)["error"]


@pytest.mark.skipif(sys.platform == "win32", reason="the shell syntax here is bash")
def test_a_timeout_stops_the_command_and_what_it_started(tmp_path):
    marker = tmp_path / "late"
    t = time.time()
    out = pc_tools.run_command(f"(sleep 3; touch {marker}) & sleep 30", str(tmp_path), 1)
    assert out["error"] == "Timed out after 1s" and time.time() - t < 10
    time.sleep(3.5)
    assert not marker.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="the shell syntax here is bash")
def test_an_app_started_in_the_background_keeps_running(tmp_path):
    marker = tmp_path / "still-here"
    out = pc_tools.run_command(f"echo started; (sleep 2; touch {marker}) &", str(tmp_path), 1)
    assert out["stdout"].startswith("started") and "still running" in out.get("error", "")
    time.sleep(2.5)
    assert marker.exists()
