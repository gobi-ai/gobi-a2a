"""The `gobi-a2a` command.

    gobi-a2a [serve] [-c a2a.yaml] [--host H] [--port P]    start the gateway (uvicorn)
    gobi-a2a check   [-c a2a.yaml]                          check the config, print each agent's address
    gobi-a2a token                                          print a new random bearer token

The config is -c/--config, else $GOBI_A2A_CONFIG, else ./a2a.yaml.
"""
from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys

from gobi_a2a import __version__
from gobi_a2a.agents import AGENTS
from gobi_a2a.config import Config, ConfigError, load


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gobi-a2a", description="A config-driven gateway of A2A coding agents for Gobi.")
    p.add_argument("--version", action="version", version=f"gobi-a2a {__version__}")
    sub = p.add_subparsers(dest="cmd")
    for name, help_ in (("serve", "start the gateway (the default)"), ("check", "check the config and print the agents")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("-c", "--config", default=None, help="the config file (default $GOBI_A2A_CONFIG or ./a2a.yaml)")
        if name == "serve":
            s.add_argument("--host", default=None, help="overrides gateway.host")
            s.add_argument("--port", type=int, default=None, help="overrides gateway.port")
    sub.add_parser("token", help="print a new random bearer token")
    return p


def config_path(arg: str | None) -> str:
    return arg or os.environ.get("GOBI_A2A_CONFIG") or "a2a.yaml"


def describe(cfg: Config) -> str:
    gw = cfg.gateway
    lines = [f"gateway  listens on {gw.host}:{gw.port}, reached at {gw.url}, "
             f"{'bearer token set' if gw.token else 'NO AUTH (local testing only)'}"]
    for name in AGENTS:
        a = cfg.agents[name]
        if not a.enabled:
            lines.append(f"{name:<8} disabled")
            continue
        extra = ""
        if name == "claude":
            extra = f"  model={a.model} effort={a.effort or '-'} permission_mode={a.permission_mode} max_turns={a.max_turns or 'none'}"
        else:
            extra = f"  command={a.command[0]}" + (f" model={a.model}" if a.model else "")
        lines.append(f"{name:<8} {gw.url}/{name}  workdir={cfg.workdir(name)}{extra}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("serve", "check", "token", "-h", "--help", "--version"):
        argv.insert(0, "serve")                   # `gobi-a2a` and `gobi-a2a -c x.yaml` serve
    args = parser().parse_args(argv)
    cmd = args.cmd
    if cmd == "token":
        print(secrets.token_hex(24))
        return
    path = config_path(getattr(args, "config", None))
    try:
        cfg = load(path)
    except ConfigError as e:
        sys.exit(f"gobi-a2a: {path}: {e}")
    if cmd == "check":
        print(describe(cfg))
        return

    import uvicorn

    from gobi_a2a.gateway import create_app

    if args.host:
        cfg.gateway.host = args.host
    if args.port:
        cfg.gateway.port = args.port
    level = cfg.gateway.log_level.upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("gobi_a2a").info("config %s\n%s", cfg.path, describe(cfg))
    uvicorn.run(create_app(cfg), host=cfg.gateway.host, port=cfg.gateway.port, log_level=cfg.gateway.log_level.lower())
