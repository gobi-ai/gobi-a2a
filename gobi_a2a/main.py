"""The `gobi-a2a` command.

    gobi-a2a [serve] [-c a2a.yaml] [--host H] [--port P]    start the gateway (uvicorn)
    gobi-a2a check   [-c a2a.yaml]                          check the config, print each agent's address and what to
                                                            enter in the Gobi app
    gobi-a2a token                                          print a new random bearer token
    gobi-a2a start   [-c a2a.yaml]                          run it in the background, now and at each login (macOS);
                                                            the first time, write the config with the agents found
    gobi-a2a stop                                           stop it, and not at login either

The config is -c/--config, else $GOBI_A2A_CONFIG, else ./a2a.yaml if there is one, else ~/.gobi-a2a/a2a.yaml.
"""
from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
from pathlib import Path

from gobi_a2a import __version__
from gobi_a2a.agents import AGENTS
from gobi_a2a.config import Config, ConfigError, load


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gobi-a2a", description="A config-driven gateway of A2A coding agents for Gobi.")
    p.add_argument("--version", action="version", version=f"gobi-a2a {__version__}")
    sub = p.add_subparsers(dest="cmd")
    for name, help_ in (("serve", "start the gateway (the default)"), ("check", "check the config and print the agents"),
                        ("start", "run the gateway in the background, now and at each login (macOS)")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("-c", "--config", default=None,
                       help="the config file (default $GOBI_A2A_CONFIG, ./a2a.yaml, or ~/.gobi-a2a/a2a.yaml)")
        if name == "serve":
            s.add_argument("--host", default=None, help="overrides gateway.host")
            s.add_argument("--port", type=int, default=None, help="overrides gateway.port")
    sub.add_parser("token", help="print a new random bearer token")
    sub.add_parser("stop", help="stop the background gateway, and not at login either")
    return p


def config_path(arg: str | None) -> str:
    from gobi_a2a.service import home
    return arg or os.environ.get("GOBI_A2A_CONFIG") or ("a2a.yaml" if Path("a2a.yaml").exists() else str(home() / "a2a.yaml"))


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


def masked(token: str) -> str:
    """Enough of a token to tell which one it is, not enough to use it."""
    return f"{token[:4]}…{token[-4:]}" if len(token) >= 16 else "*" * 8


def gobi_app(cfg: Config, reveal: bool = False) -> str:
    """What to enter in the Gobi app, per enabled agent: its address, and the token it wants (masked unless `reveal`)."""
    gw = cfg.gateway
    agents = {name: cfg.agents[name].name or getattr(AGENTS[name], "NAME", name) for name in AGENTS if cfg.agents[name].enabled}
    if not agents:
        return "No agent is enabled: set enabled: true on one in a2a.yaml."
    tokens = {name: cfg.token(name) for name in agents}
    shared = len(set(tokens.values())) == 1
    width = max(len("Bearer token"), *(len(label) for label in agents.values()))
    lines = ["Agent URLs ready. In the Gobi app: Workers > gear > Remote agent, add each address with the token:"]
    for name, label in agents.items():
        own = "" if shared else f"  token {(tokens[name] if reveal else masked(tokens[name])) if tokens[name] else 'none'}"
        lines.append(f"  • {label:<{width}}  {gw.url}/{name}{own}")
    token = next(iter(tokens.values()))
    if shared and token and reveal:
        lines.append(f"    {'Bearer token':<{width}}  {token}")
    elif shared and token:
        lines.append(f"    {'Bearer token':<{width}}  {masked(token)}  (the full one is gateway.token: echo $A2A_TOKEN)")
    elif shared:
        lines.append(f"    {'Bearer token':<{width}}  none: anyone with the address can use these agents (local testing only)")
    if not gw.url.startswith("https://"):
        lines.append(f"  ! {gw.url} is not https: Gobi only calls public https addresses. "
                     f"Set gateway.public_url in {cfg.path or 'a2a.yaml'} to your tunnel's address and run this again.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("serve", "check", "token", "start", "stop", "-h", "--help", "--version"):
        argv.insert(0, "serve")                   # `gobi-a2a` and `gobi-a2a -c x.yaml` serve
    args = parser().parse_args(argv)
    cmd = args.cmd
    if cmd == "token":
        print(secrets.token_hex(24))
        return
    if cmd == "stop":
        from gobi_a2a.service import stop
        stop()
        return
    path = config_path(getattr(args, "config", None))
    if cmd == "start":
        from gobi_a2a.service import start

        def checked(p: Path) -> Config:
            try:
                return load(p)
            except ConfigError as e:
                sys.exit(f"gobi-a2a: {p}: {e}")
        start(Path(path).expanduser().resolve(), load=checked, describe=gobi_app)
        return
    try:
        cfg = load(path)
    except ConfigError as e:
        sys.exit(f"gobi-a2a: {path}: {e}")
    if cmd == "check":
        print(describe(cfg))
        print()
        print(gobi_app(cfg))
        return

    import uvicorn

    from gobi_a2a.gateway import create_app

    if args.host:
        cfg.gateway.host = args.host
    if args.port:
        cfg.gateway.port = args.port
    level = cfg.gateway.log_level.upper()
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("gobi_a2a").info("config %s\n%s\n\n%s", cfg.path, describe(cfg), gobi_app(cfg))
    uvicorn.run(create_app(cfg), host=cfg.gateway.host, port=cfg.gateway.port, log_level=cfg.gateway.log_level.lower())
