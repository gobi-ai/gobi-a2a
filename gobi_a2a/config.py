"""a2a.yaml: the gateway's address and token, and which agents it mounts with what settings.

    gateway:
      host: 127.0.0.1
      port: 8000
      public_url: https://my-mac.tail1234.ts.net # what Agent Cards name; default http://host:port
      token: ${A2A_TOKEN}                   # the bearer token callers present
      no_auth: false                        # true runs without a token: local testing only
      workdir: ./work                       # each agent gets <workdir>/<agent>, each A2A context a folder under that
    agents:
      claude: {enabled: true, model: claude-opus-5-5, ...}
      cursor: {enabled: false, ...}

A string may name environment variables: ${NAME}, or ${NAME:-fallback}. An unset one with no fallback is empty, and an
empty value is the same as leaving the key out. Relative paths are relative to the config file's folder. An unknown key
or agent, or a value that cannot be used, stops the gateway at start with a ConfigError that says which.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from gobi_a2a.agents import AGENTS
from gobi_a2a.agents.common import AgentConfig

DEFAULT_PORT = 8000
ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(ValueError):
    """a2a.yaml cannot be used as it is. The words say what to change."""


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    public_url: str | None = None
    token: str | None = None
    no_auth: bool = False
    workdir: Path = Path("work")
    log_level: str = "info"

    @field_validator("public_url", "token", mode="before")
    @classmethod
    def _empty_is_none(cls, v: Any) -> Any:
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("public_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^https?://[^/\s]+", v):
            raise ValueError(f"{v!r} is not an http(s) URL")
        return v.rstrip("/") if v else v

    @field_validator("port")
    @classmethod
    def _port(cls, v: int) -> int:
        if not 0 < v < 65536:
            raise ValueError(f"{v} is not a TCP port")
        return v

    @property
    def url(self) -> str:
        """Where callers reach the gateway: the public URL, or the address it listens on."""
        return self.public_url or f"http://{self.host}:{self.port}"


class Config:
    """The whole file, checked: the gateway's settings, and each known agent's settings (enabled or not)."""

    def __init__(self, gateway: GatewayConfig, agents: dict[str, AgentConfig], path: Path | None = None):
        self.gateway, self.agents, self.path = gateway, agents, path

    @property
    def enabled(self) -> dict[str, AgentConfig]:
        return {n: a for n, a in self.agents.items() if a.enabled}

    def workdir(self, name: str) -> Path:
        return self.agents[name].workdir or self.gateway.workdir / name

    def token(self, name: str) -> str | None:
        """The bearer token requests to this agent need: its own, else the gateway's. None: open."""
        return self.agents[name].token or self.gateway.token


def interpolate(value: Any, env: dict[str, str] | os._Environ = os.environ) -> Any:
    """${NAME} and ${NAME:-fallback} in every string, recursively."""
    if isinstance(value, str):
        return ENV_REF.sub(lambda m: env.get(m.group(1)) or (m.group(2) or ""), value)
    if isinstance(value, list):
        return [interpolate(v, env) for v in value]
    if isinstance(value, dict):
        return {k: interpolate(v, env) for k, v in value.items()}
    return value


def _where(e: ValidationError, prefix: str) -> str:
    return "; ".join(f"{prefix}{'.'.join(str(p) for p in err['loc'])}: {err['msg'].removeprefix('Value error, ')}"
                     for err in e.errors())


def _set(block: dict) -> dict:
    """A block with its empty values left out, so they take their defaults (`effort:` or `${UNSET}` alike)."""
    return {k: v for k, v in block.items() if v is not None and not (isinstance(v, str) and not v.strip())}


def _resolve(p: Path, base: Path) -> Path:
    p = p.expanduser()
    return (p if p.is_absolute() else base / p).resolve()


def parse(raw: Any, *, base: Path | None = None, env: dict[str, str] | os._Environ = os.environ,
          path: Path | None = None) -> Config:
    """A loaded YAML document (or {}) -> Config. `base` is where relative paths start (default: the current folder)."""
    raw = interpolate(raw if raw is not None else {}, env)
    if not isinstance(raw, dict):
        raise ConfigError("the config must be a mapping with `gateway:` and `agents:`")
    if extra := set(raw) - {"gateway", "agents"}:
        raise ConfigError(f"unknown top-level key(s): {', '.join(sorted(map(str, extra)))} (expected gateway, agents)")
    base = (base or Path.cwd()).resolve()
    try:
        gw = GatewayConfig.model_validate(_set(raw.get("gateway") or {}))
    except ValidationError as e:
        raise ConfigError(_where(e, "gateway.")) from None
    gw.workdir = _resolve(gw.workdir, base)
    if gw.token is None and not gw.no_auth:
        raise ConfigError("gateway.token is empty: set it (e.g. token: ${A2A_TOKEN} and export A2A_TOKEN=$(openssl rand -hex 24)), "
                          "or set gateway.no_auth: true for local testing only")

    blocks = raw.get("agents") or {}
    if not isinstance(blocks, dict):
        raise ConfigError("agents must be a mapping of agent name to its settings")
    if unknown := set(blocks) - set(AGENTS):
        raise ConfigError(f"unknown agent(s): {', '.join(sorted(map(str, unknown)))} (known: {', '.join(AGENTS)})")
    agents: dict[str, AgentConfig] = {}
    for name, module in AGENTS.items():
        block = blocks.get(name)
        if block is not None and not isinstance(block, dict):
            raise ConfigError(f"agents.{name} must be a mapping (e.g. enabled: true)")
        try:
            cfg = module.Config.model_validate(_set(block or {}))
        except ValidationError as e:
            raise ConfigError(_where(e, f"agents.{name}.")) from None
        if cfg.workdir is not None:
            cfg.workdir = _resolve(cfg.workdir, base)
        agents[name] = cfg
    return Config(gw, agents, path)


def load(path: str | Path, *, env: dict[str, str] | os._Environ = os.environ) -> Config:
    p = Path(path).expanduser()
    try:
        raw = yaml.safe_load(p.read_text())
    except FileNotFoundError:
        raise ConfigError(f"{p} not found (copy a2a.example.yaml to a2a.yaml to start)") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"{p} is not valid YAML: {e}") from None
    return parse(raw, base=p.resolve().parent, env=env, path=p.resolve())
