"""`gobi-a2a start`'s parts: the config it writes, the agents it finds, Tailscale Funnel, and the LaunchAgent plist."""
from __future__ import annotations

import json
import plistlib
import stat
import subprocess
import sys

import pytest

from gobi_a2a import service
from gobi_a2a.config import load


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GOBI_A2A_HOME", str(tmp_path / "home"))


def test_the_config_it_writes_enables_only_what_was_found(tmp_path):
    p = tmp_path / "home" / "a2a.yaml"
    here = {"claude": True, "cursor": False, "codex": True, "omp": False, "copilot": False}
    service.write_config(p, here, "https://mac.tail1.ts.net")
    cfg = load(p)
    assert list(cfg.enabled) == ["claude", "codex"]
    assert cfg.gateway.public_url == "https://mac.tail1.ts.net" and len(cfg.gateway.token) == 48
    assert cfg.workdir("claude") == tmp_path / "home" / "work" / "claude"
    assert "cursor.com/install" in p.read_text()            # what to do about the ones not found
    assert stat.S_IMODE(p.stat().st_mode) == 0o600          # the token is in it


def test_without_a_tunnel_the_config_still_loads_and_check_says_it_is_not_https(tmp_path):
    p = tmp_path / "a2a.yaml"
    service.write_config(p, dict.fromkeys(["claude", "cursor", "codex", "omp", "copilot"], False), None)
    assert load(p).gateway.url == "http://127.0.0.1:8000"


def test_it_finds_a_cli_agent_by_its_command_on_the_path(tmp_path):
    for name in ("cursor-agent", "omp"):
        (tmp_path / name).write_text("#!/bin/sh\n")
        (tmp_path / name).chmod(0o755)
    here = service.found(str(tmp_path), claude=False)
    assert here == {"claude": False, "cursor": True, "codex": False, "omp": True, "copilot": False}


def test_vs_codes_copilot_shim_is_not_copilot(tmp_path):
    shim = tmp_path / "globalStorage" / "github.copilot-chat" / "copilotCli"
    shim.mkdir(parents=True)
    (shim / "copilot").write_text("#!/bin/sh\n")
    (shim / "copilot").chmod(0o755)
    assert service.found(str(shim), claude=True)["copilot"] is False


def test_claude_counts_as_found_when_signed_in(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    no = lambda *a, **k: subprocess.CompletedProcess(a, 44)
    assert service.claude_signed_in({}, run=no) is False
    assert service.claude_signed_in({"ANTHROPIC_API_KEY": "k"}, run=no) is True
    assert service.claude_signed_in({}, run=lambda *a, **k: subprocess.CompletedProcess(a, 0)) is True


def test_search_path_adds_the_folders_agents_install_to(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".bun" / "bin").mkdir(parents=True)
    for v in ("v20.1.0", "v22.3.0"):
        (tmp_path / ".nvm" / "versions" / "node" / v / "bin").mkdir(parents=True)
    dirs = service.search_path("/usr/bin:/nowhere:/usr/bin").split(":")
    assert dirs[0] == "/usr/bin" and "/nowhere" not in dirs and dirs.count("/usr/bin") == 1
    assert str(tmp_path / ".bun" / "bin") in dirs
    assert dirs[-2:] == [str(tmp_path / ".nvm/versions/node" / v / "bin") for v in ("v22.3.0", "v20.1.0")]


class Tailscale:
    """Stands in for the tailscale CLI: its status, what it serves, and the funnel commands it was given."""

    def __init__(self, state="Running", proxy=None, funnel=False, rc=0, out=""):
        self.state, self.proxy, self.allow, self.rc, self.out, self.calls = state, proxy, funnel, rc, out, []

    def __call__(self, argv, **kw):
        self.calls.append(argv[1:])
        host = "mac.tail1.ts.net:443"
        if argv[1:] == ["status", "--json"]:
            out = {"BackendState": self.state, "Self": {"DNSName": "mac.tail1.ts.net."}}
        elif argv[1:3] == ["serve", "status"]:
            out = {"Web": {host: {"Handlers": {"/": {"Proxy": self.proxy}}}}, "AllowFunnel": {host: self.allow}} \
                if self.proxy else {}
            return subprocess.CompletedProcess(argv, 1, json.dumps(out), "")    # it exits 1, as the real one does
        else:
            return subprocess.CompletedProcess(argv, self.rc, self.out, "")
        return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")


@pytest.fixture
def ts(tmp_path):
    (tmp_path / "tailscale").write_text("#!/bin/sh\n")
    (tmp_path / "tailscale").chmod(0o755)
    return str(tmp_path)


def test_funnel_turns_on_and_gives_the_ts_net_address(ts):
    run = Tailscale()
    assert service.funnel(8000, ts, run) == ("https://mac.tail1.ts.net", None)
    assert ["funnel", "--bg", "8000"] in run.calls


def test_funnel_already_on_for_the_gateway_is_left_alone(ts):
    run = Tailscale(proxy="http://127.0.0.1:8000", funnel=True)
    assert service.funnel(8000, ts, run) == ("https://mac.tail1.ts.net", None)
    assert not any(c[0] == "funnel" for c in run.calls)


def test_funnel_serving_something_else_is_not_taken_over(ts):
    run = Tailscale(proxy="http://127.0.0.1:3000", funnel=True)
    url, note = service.funnel(8000, ts, run)
    assert url is None and "left as it is" in note and not any(c[0] == "funnel" for c in run.calls)


def test_funnel_says_why_it_could_not(ts, monkeypatch):
    monkeypatch.setattr(service, "TS_APP", "/nowhere")
    assert "Install Tailscale" in service.funnel(8000, "/nowhere", Tailscale())[1]
    assert "not signed in" in service.funnel(8000, ts, Tailscale(state="NeedsLogin"))[1]
    out = "Funnel is not enabled on your tailnet.\nTo enable, visit: https://login.tailscale.com/f/funnel?node=x"
    url, note = service.funnel(8000, ts, Tailscale(rc=1, out=out))
    assert url is None and "https://login.tailscale.com/f/funnel?node=x" in note


def test_the_plist_runs_this_python_with_the_path_and_the_variables_the_config_names(tmp_path):
    p = tmp_path / "a2a.yaml"
    p.write_text("gateway: {token: '${A2A_TOKEN}', public_url: '${A2A_PUBLIC_URL}'}\n")
    pl = plistlib.loads(service.plist(p, "/a:/b", env={"A2A_TOKEN": "t", "HOME": "/h", "A2A_PUBLIC_URL": ""}))
    assert pl["ProgramArguments"] == [sys.executable, "-m", "gobi_a2a", "serve", "-c", str(p)]
    assert pl["EnvironmentVariables"] == {"PATH": "/a:/b", "A2A_TOKEN": "t"}
    assert pl["RunAtLoad"] and pl["KeepAlive"] == {"SuccessfulExit": False}
    assert pl["StandardErrorPath"] == str(tmp_path / "home" / "gateway.log")


def test_tailscale_after_the_first_start_fills_the_empty_public_url(tmp_path):
    p = tmp_path / "a2a.yaml"
    service.write_config(p, dict.fromkeys(["claude", "cursor", "codex", "omp", "copilot"], True), None)
    before = p.read_text()
    assert service.fill_public_url(p, "https://mac.tail1.ts.net")
    assert load(p).gateway.public_url == "https://mac.tail1.ts.net"
    assert [l for l in before.splitlines() if "public_url" not in l] == \
           [l for l in p.read_text().splitlines() if "public_url" not in l]


def test_a_public_url_someone_wrote_is_not_overwritten(tmp_path):
    p = tmp_path / "a2a.yaml"
    p.write_text("gateway:\n  public_url: ${A2A_PUBLIC_URL}\n  token: t\n")
    assert not service.fill_public_url(p, "https://mac.tail1.ts.net")
    assert "${A2A_PUBLIC_URL}" in p.read_text()
