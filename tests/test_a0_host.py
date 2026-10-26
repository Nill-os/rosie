# board #840 owner-windows-a0-brain
import json
import subprocess

import a0
import a0_host
import config


def fake_docker(ps, inspect):
    def run(*args, timeout=20):
        if args[0] == "ps":
            return subprocess.CompletedProcess(args, 0, ps, "")
        return subprocess.CompletedProcess(args, 0, json.dumps([inspect]), "")
    return run


def test_it_finds_a_differently_named_container_its_port_and_its_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(a0_host, "FILE", tmp_path / "a0_host.json")
    monkeypatch.setattr(a0_host, "_docker", fake_docker(
        "ollama-web\tghcr.io/open-webui\trunning\nmy-a0\tagent0ai/agent-zero:latest\trunning\n",
        {"HostConfig": {"PortBindings": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "50001"}]}},
         "Mounts": [{"Type": "bind", "Source": "C:\\Users\\her\\agent-zero\\usr", "Destination": "/a0/usr"}]}))
    got = a0_host.detect()
    assert got["container"] == "my-a0" and got["url"] == "http://127.0.0.1:50001" and got["usr"] == "C:\\Users\\her\\agent-zero\\usr"
    assert a0_host.info() == {"container": "my-a0", "url": "http://127.0.0.1:50001", "usr": "C:\\Users\\her\\agent-zero\\usr"}


def test_settings_win_and_without_anything_the_original_layout_applies(tmp_path, monkeypatch):
    monkeypatch.setattr(a0_host, "FILE", tmp_path / "none.json")
    assert a0_host.info() == a0_host.DEFAULT
    cfg = config.load()
    cfg["a0_url"] = "http://127.0.0.1:50999"
    config.save(cfg)
    assert a0_host.url() == "http://127.0.0.1:50999"


def test_no_docker_or_no_container_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(a0_host, "FILE", tmp_path / "a0_host.json")

    def missing(*a, **k):
        raise FileNotFoundError("docker")
    monkeypatch.setattr(a0_host, "_docker", missing)
    assert a0_host.detect() == {"found": False, "why": "Docker isn't installed or isn't running."}
    monkeypatch.setattr(a0_host, "_docker", fake_docker("ollama\tollama/ollama\trunning\n", {}))
    assert a0_host.detect()["why"] == "No Agent Zero container on this PC."
    assert not (tmp_path / "a0_host.json").exists()


def test_a_volume_install_still_works_without_rosies_prompt_files(tmp_path, monkeypatch):
    monkeypatch.setattr(a0_host, "FILE", tmp_path / "a0_host.json")
    monkeypatch.setattr(a0_host, "_docker", fake_docker("agent-zero\tagent0ai/agent-zero\trunning\n",
                                                        {"Mounts": [{"Type": "volume", "Name": "a0data", "Destination": "/a0/usr"}]}))
    got = a0_host.detect()
    assert got["found"] and "usr" not in got and "can't add her rules" in got["usr_note"]


def test_relocate_moves_every_agent_zero_path_to_this_pcs_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(a0_host, "FILE", tmp_path / "a0_host.json")
    (tmp_path / "a0_host.json").write_text(json.dumps({"container": "my-a0", "url": "http://127.0.0.1:50001", "usr": str(tmp_path / "usr")}))
    for name in ("INCLUDE", "INCLUDE_CLAUDE", "INCLUDE_TOOLS", "EXT_DIR", "TURNS", "CONTAINER", "URL"):
        monkeypatch.setattr(a0, name, getattr(a0, name))
    a0.relocate()
    assert a0.INCLUDE == tmp_path / "usr" / "workdir" / "rosie.promptinclude.md" and a0.TURNS == tmp_path / "usr" / "rosie_turns"
    assert a0.EXT_DIR == tmp_path / "usr" / "extensions" / "python" and a0.CONTAINER == "my-a0" and a0.URL == "http://127.0.0.1:50001"


def test_the_mcp_server_listens_where_agent_zero_can_reach_it():
    import a0_mcp
    assert a0_mcp.HOST == ("172.17.0.1" if a0_host.LINUX else "127.0.0.1")
