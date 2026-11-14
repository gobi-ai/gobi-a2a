"""a2a.yaml: defaults, environment references, paths, and the mistakes that stop the gateway at start."""
from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk.types import EffortLevel
from typing import get_args

from gobi_a2a.agents.common import EFFORTS
from gobi_a2a.config import ConfigError, load, parse

ROOT = Path(__file__).parent.parent


def test_the_example_config_loads_with_claude_on_and_the_scaffolds_off():
    cfg = load(ROOT / "a2a.example.yaml", env={"A2A_TOKEN": "t", "A2A_PUBLIC_URL": "https://me.ngrok.app/"})
    gw = cfg.gateway
    assert (gw.host, gw.port, gw.token, gw.url) == ("127.0.0.1", 8000, "t", "https://me.ngrok.app")
    assert gw.workdir == (ROOT / "work").resolve()
    assert list(cfg.enabled) == ["claude"] and set(cfg.agents) == {"claude", "cursor", "codex", "aider", "omp"}
    c = cfg.agents["claude"]
    assert (c.model, c.effort, c.thinking, c.permission_mode, c.max_turns) == ("claude-opus-5-5", None, None, "default", None)
    assert "Bash" not in c.allowed_tools
    assert cfg.agents["aider"].command[0] == "aider" and cfg.agents["omp"].effort_args == ["--thinking", "{effort}"]


def test_without_a_public_url_the_cards_name_the_listening_address():
    cfg = load(ROOT / "a2a.example.yaml", env={"A2A_TOKEN": "t"})
    assert cfg.gateway.public_url is None and cfg.gateway.url == "http://127.0.0.1:8000"


def test_an_empty_file_is_all_defaults_but_needs_a_token(tmp_path):
    with pytest.raises(ConfigError, match="gateway.token is empty"):
        parse(None, base=tmp_path, env={})
    cfg = parse({"gateway": {"no_auth": True}}, base=tmp_path, env={})
    assert cfg.gateway.port == 8000 and cfg.gateway.token is None and list(cfg.enabled) == ["claude"]
    assert cfg.workdir("claude") == (tmp_path / "work" / "claude").resolve()


def test_environment_references_and_their_fallbacks(tmp_path):
    raw = {"gateway": {"token": "${TOK}", "port": "${PORT:-9001}", "public_url": "https://${HOST}/"},
           "agents": {"claude": {"model": "${MODEL:-claude-sonnet-5}", "system_prompt": "${NOPE}"}}}
    cfg = parse(raw, base=tmp_path, env={"TOK": "abc", "HOST": "x.ngrok.app"})
    assert (cfg.gateway.token, cfg.gateway.port, cfg.gateway.url) == ("abc", 9001, "https://x.ngrok.app")
    assert cfg.agents["claude"].model == "claude-sonnet-5" and cfg.agents["claude"].system_prompt is None
    with pytest.raises(ConfigError, match="token is empty"):
        parse(raw, base=tmp_path, env={"HOST": "x.ngrok.app"})


def test_relative_paths_start_at_the_config_files_folder_and_an_agent_may_have_its_own(tmp_path):
    (tmp_path / "a2a.yaml").write_text("gateway: {no_auth: true, workdir: ./w}\nagents: {cursor: {enabled: true, workdir: ~/cw}}\n")
    cfg = load(tmp_path / "a2a.yaml", env={})
    assert cfg.workdir("claude") == (tmp_path / "w" / "claude").resolve()
    assert cfg.workdir("cursor") == (Path.home() / "cw").resolve()
    assert list(cfg.enabled) == ["claude", "cursor"]


def test_an_agent_token_overrides_the_gateways(tmp_path):
    cfg = parse({"gateway": {"token": "g"}, "agents": {"cursor": {"enabled": True, "token": "c"}}}, base=tmp_path, env={})
    assert cfg.token("claude") == "g" and cfg.token("cursor") == "c"


def test_claudes_settings(tmp_path):
    cfg = parse({"gateway": {"no_auth": True}, "agents": {"claude": {
        "model": " claude-sonnet-5 ", "effort": "XHigh", "thinking": 16000, "permission_mode": "bypassPermissions",
        "allowed_tools": "Read, Bash", "max_turns": 40}}}, base=tmp_path, env={})
    c = cfg.agents["claude"]
    assert (c.model, c.effort, c.thinking, c.permission_mode, c.allowed_tools, c.max_turns) == \
        ("claude-sonnet-5", "xhigh", 16000, "bypassPermissions", ["Read", "Bash"], 40)


@pytest.mark.parametrize("raw, where", [
    ({"gateway": {"no_auth": True, "prot": 1}}, "gateway.prot"),
    ({"gateway": {"no_auth": True, "port": 70000}}, "gateway.port"),
    ({"gateway": {"no_auth": True, "public_url": "me.ngrok.app"}}, "gateway.public_url"),
    ({"gateway": {"no_auth": True}, "agents": {"gemini": {}}}, "unknown agent"),
    ({"gateway": {"no_auth": True}, "agents": {"claude": {"modle": "x"}}}, "agents.claude.modle"),
    ({"gateway": {"no_auth": True}, "agents": {"claude": {"permission_mode": "yolo"}}}, "bypassPermissions"),
    ({"gateway": {"no_auth": True}, "agents": {"claude": {"effort": "extreme"}}}, "agents.claude.effort"),
    ({"gateway": {"no_auth": True}, "agents": {"claude": {"effort": "0.55"}}}, "agents.claude.effort"),
    ({"gateway": {"no_auth": True}, "agents": {"claude": {"thinking": "on"}}}, "agents.claude.thinking"),
    ({"gateway": {"no_auth": True}, "agents": {"claude": {"thinking": 0}}}, "agents.claude.thinking"),
    ({"gateway": {"no_auth": True}, "agents": {"claude": {"model": "opus; rm -rf /"}}}, "agents.claude.model"),
    ({"gateway": {"no_auth": True}, "agents": {"claude": {"max_turns": -1}}}, "agents.claude.max_turns"),
    ({"gateway": {"no_auth": True}, "agents": {"cursor": {"command": []}}}, "agents.cursor.command"),
    ({"gateway": {"no_auth": True}, "agents": {"aider": "on"}}, "agents.aider must be a mapping"),
    ({"gateway": {"no_auth": True}, "agnts": {}}, "unknown top-level"),
])
def test_a_mistake_stops_the_gateway_at_start_and_says_where(tmp_path, raw, where):
    with pytest.raises(ConfigError, match=where.replace(".", r"\.")):
        parse(raw, base=tmp_path, env={})


def test_a_missing_or_broken_file_says_so(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load(tmp_path / "a2a.yaml")
    (tmp_path / "a2a.yaml").write_text("gateway: [unclosed\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load(tmp_path / "a2a.yaml")


def test_the_efforts_are_the_claude_agent_sdks():
    assert EFFORTS == get_args(EffortLevel)
