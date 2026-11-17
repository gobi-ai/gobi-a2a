"""The gobi-a2a command: which config it reads, what `check` prints, and how `serve` starts uvicorn."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from gobi_a2a import main as cli

ROOT = Path(__file__).parent.parent


@pytest.fixture
def conf(tmp_path, monkeypatch):
    monkeypatch.delenv("GOBI_A2A_CONFIG", raising=False)
    p = tmp_path / "a2a.yaml"
    p.write_text("gateway: {token: t, public_url: 'https://me.ngrok.app', port: 8000}\n"
                 "agents: {cursor: {enabled: true}}\n")
    return p


def test_check_prints_each_agents_address(conf, capsys):
    cli.main(["check", "-c", str(conf)])
    out = capsys.readouterr().out
    assert "https://me.ngrok.app/claude" in out and "https://me.ngrok.app/cursor" in out and "command=cursor-agent" in out
    assert re.search(r"codex\s+disabled", out) and "bearer token set" in out


def test_serve_starts_uvicorn_on_the_configs_address_or_the_flags(conf, monkeypatch):
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    cli.main(["serve", "-c", str(conf)])
    cli.main(["serve", "-c", str(conf), "--host", "0.0.0.0", "--port", "9000"])
    (app, kw), (_, kw2) = calls
    assert (kw["host"], kw["port"]) == ("127.0.0.1", 8000) and (kw2["host"], kw2["port"]) == ("0.0.0.0", 9000)
    assert list(app.state.agents) == ["claude", "cursor"]


def test_serve_is_the_default_and_the_config_may_come_from_the_environment(conf, monkeypatch):
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append(kw))
    monkeypatch.setenv("GOBI_A2A_CONFIG", str(conf))
    cli.main([])
    assert calls[0]["port"] == 8000


def test_a_bad_config_exits_with_words_not_a_traceback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GOBI_A2A_HOME", str(tmp_path / "home"))
    with pytest.raises(SystemExit, match="a2a.yaml not found"):
        cli.main(["check"])
    (tmp_path / "a2a.yaml").write_text("agents: {claude: {permission_mode: yolo}}\ngateway: {token: t}\n")
    with pytest.raises(SystemExit, match="agents.claude.permission_mode"):
        cli.main(["serve"])


def test_token_prints_a_fresh_random_token(capsys):
    cli.main(["token"])
    cli.main(["token"])
    a, b = capsys.readouterr().out.split()
    assert re.fullmatch(r"[0-9a-f]{48}", a) and a != b


def test_the_installed_command_and_python_dash_m_work():
    out = subprocess.run([sys.executable, "-m", "gobi_a2a", "--version"], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "gobi-a2a 0.1.0"
    exe = Path(sys.executable).parent / "gobi-a2a"
    assert subprocess.run([str(exe), "check", "-c", str(ROOT / "a2a.example.yaml")], capture_output=True, text=True,
                          env={"A2A_TOKEN": "x", "PATH": "/usr/bin:/bin"}).returncode == 0


TOKEN = "3f9c2a1b" + "0" * 32 + "5d8eb7e1"


def test_check_says_what_to_enter_in_the_gobi_app_without_printing_the_token(tmp_path, capsys):
    p = tmp_path / "a2a.yaml"
    p.write_text(f"gateway: {{token: {TOKEN}, public_url: 'https://my-mac.tail1234.ts.net/'}}\n"
                 "agents: {cursor: {enabled: true}}\n")
    cli.main(["check", "-c", str(p)])
    out = capsys.readouterr().out
    assert "Workers > gear > Remote agent" in out
    assert re.search(r"• Claude Code +https://my-mac\.tail1234\.ts\.net/claude\n", out)
    assert re.search(r"• Cursor Agent +https://my-mac\.tail1234\.ts\.net/cursor\n", out)
    assert re.search(r"Bearer token +3f9c…b7e1 ", out) and TOKEN not in out and "not https" not in out


def test_check_names_each_agents_own_token_and_warns_when_gobi_cannot_reach_it(tmp_path, capsys):
    p = tmp_path / "a2a.yaml"
    p.write_text(f"gateway: {{token: {TOKEN}}}\nagents: {{codex: {{enabled: true, token: short}}}}\n")
    cli.main(["check", "-c", str(p)])
    out = capsys.readouterr().out
    assert "http://127.0.0.1:8000/claude  token 3f9c…b7e1" in out and "http://127.0.0.1:8000/codex  token ********" in out
    assert "Bearer token" not in out and "is not https: Gobi only calls public https addresses" in out


def test_check_says_when_there_is_no_token_at_all(tmp_path, capsys):
    p = tmp_path / "a2a.yaml"
    p.write_text("gateway: {no_auth: true, public_url: 'https://x.trycloudflare.com'}\n")
    cli.main(["check", "-c", str(p)])
    assert "none: anyone with the address can use these agents" in capsys.readouterr().out


def test_the_config_is_the_one_here_else_the_one_start_writes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GOBI_A2A_CONFIG", raising=False)
    monkeypatch.setenv("GOBI_A2A_HOME", str(tmp_path / "home"))
    assert cli.config_path(None) == str(tmp_path / "home" / "a2a.yaml")
    (tmp_path / "a2a.yaml").write_text("")
    assert cli.config_path(None) == "a2a.yaml" and cli.config_path("x.yaml") == "x.yaml"


def test_start_shows_the_whole_token_check_does_not(tmp_path):
    p = tmp_path / "a2a.yaml"
    p.write_text(f"gateway: {{token: {TOKEN}, public_url: 'https://my-mac.tail1234.ts.net'}}\n")
    from gobi_a2a.config import load
    assert re.search(rf"Bearer token +{TOKEN}\n?", cli.gobi_app(load(p), reveal=True))
    assert TOKEN not in cli.gobi_app(load(p))
