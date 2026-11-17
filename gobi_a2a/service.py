"""`gobi-a2a start` and `stop`: the gateway as a macOS LaunchAgent, set up the first time.

    gobi-a2a start   the first time, writes ~/.gobi-a2a/a2a.yaml: a new token, and only the agents found on this Mac
                     enabled. Turns on Tailscale Funnel when Tailscale is signed in, registers the LaunchAgent (it runs
                     now, and again at each login), waits until the gateway answers, and prints what to enter in Gobi.
                     Run again, it restarts the gateway with the config as it is now.
    gobi-a2a stop    stops the gateway and unregisters it, so it does not come back at login.

launchd starts the gateway with almost nothing on PATH, so the plist carries the PATH found here (with the folders
bun, nvm and the agents' installers use), and the environment variables the config names that are set here.
$GOBI_A2A_HOME moves ~/.gobi-a2a.
"""
from __future__ import annotations

import glob
import json
import os
import plistlib
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable

from gobi_a2a.agents import AGENTS, copilot
from gobi_a2a.config import ENV_REF, Config

LABEL = "ai.gobi.a2a"
TS_APP = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
EXTRA_PATH = ["~/.local/bin", "~/.bun/bin", "~/.cursor/bin", "/opt/homebrew/bin", "/usr/local/bin"]
INSTALL = {                                   # what to do about an agent that is not found
    "claude": "sign in: run `claude`, then /login (or set ANTHROPIC_API_KEY)",
    "cursor": "install: curl https://cursor.com/install -fsS | bash, then cursor-agent login",
    "codex": "install: npm install -g @openai/codex, then codex login",
    "omp": "install: bun install -g @oh-my-pi/pi-coding-agent, then omp login",
    "copilot": "install: npm install -g @github/copilot, then copilot login",
}
Run = Callable[..., subprocess.CompletedProcess]


def home() -> Path:
    return Path(os.environ.get("GOBI_A2A_HOME") or "~/.gobi-a2a").expanduser()


def plist_path() -> Path:
    return Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()


def search_path(path: str | None = None) -> str:
    """This shell's PATH, then the folders agents install to that it lacks (nvm's newest node last of all)."""
    dirs = (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep)
    dirs += [os.path.expanduser(d) for d in EXTRA_PATH]
    dirs += sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin")), reverse=True)
    seen: list[str] = []
    for d in dirs:
        if d and d not in seen and os.path.isdir(d):
            seen.append(d)
    return os.pathsep.join(seen)


def claude_signed_in(env=os.environ, run: Run = subprocess.run) -> bool:
    """Claude Code runs from the SDK's own copy, so what matters is a sign-in: a key, a token, or Claude Code's login."""
    if env.get("ANTHROPIC_API_KEY") or env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    if Path("~/.claude/.credentials.json").expanduser().exists():
        return True
    return run(["security", "find-generic-password", "-s", "Claude Code-credentials"],
               capture_output=True).returncode == 0


def found(path: str, *, claude: bool) -> dict[str, bool]:
    """Each agent: is it here? Claude when signed in; the others when their command is on `path`."""
    out = {}
    for name, module in AGENTS.items():
        if name == "claude":
            out[name] = claude
            continue
        where = copilot.without_shim(path) if name == "copilot" else path
        out[name] = shutil.which(module.Config().command[0], path=where) is not None
    return out


def write_config(path: Path, agents: dict[str, bool], public_url: str | None, port: int = 8000) -> None:
    lines = ["# gobi-a2a: written by `gobi-a2a start`, which enabled the agents it found. Edit, then run it again.",
             "# Every setting: https://github.com/gobi-ai/gobi-a2a/blob/main/docs/configuration.md",
             "gateway:",
             f"  port: {port}",
             f"  public_url: {public_url or ''}" + ("" if public_url else "   # your tunnel's https address"),
             f"  token: {secrets.token_hex(24)}   # the bearer token Gobi presents",
             "  workdir: ./work",
             "agents:"]
    for name, here in agents.items():
        lines.append(f"  {name}: {{enabled: {'true' if here else 'false'}}}" + ("" if here else f"   # {INSTALL[name]}"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)


def fill_public_url(path: Path, url: str) -> bool:
    """Write `url` into a config whose gateway.public_url is left empty, keeping the rest as it is. Whether it did."""
    lines = path.read_text().splitlines(keepends=True)
    for i, line in enumerate(lines):
        if re.fullmatch(r"  public_url:[ \t]*(#.*)?\n?", line):
            lines[i] = f"  public_url: {url}\n"
            path.write_text("".join(lines))
            return True
    return False


def _tailscale(path: str) -> str | None:
    return shutil.which("tailscale", path=path) or (TS_APP if os.path.exists(TS_APP) else None)


def _json(run: Run, argv: list[str]) -> dict:
    try:
        # `serve status --json` prints the state and still exits 1, so the output is what counts
        return json.loads(run(argv, capture_output=True, text=True, timeout=10).stdout or "{}")
    except (subprocess.TimeoutExpired, ValueError):
        return {}


def funnel(port: int, path: str, run: Run = subprocess.run) -> tuple[str | None, str | None]:
    """Tailscale Funnel from this Mac's https://<name>.ts.net to the gateway: (its URL, or None; a note, or None)."""
    ts = _tailscale(path)
    if ts is None:
        return None, ("No Tailscale here: Gobi needs a public https address. Install Tailscale "
                      "(https://tailscale.com/download), sign in, and run `gobi-a2a start` again.")
    status = _json(run, [ts, "status", "--json"])
    host = ((status.get("Self") or {}).get("DNSName") or "").rstrip(".")
    if status.get("BackendState") != "Running" or not host:
        return None, "Tailscale is not signed in: open it, sign in, and run `gobi-a2a start` again."
    url, target = f"https://{host}", f"http://127.0.0.1:{port}"
    serve = _json(run, [ts, "serve", "status", "--json"])
    proxy = ((serve.get("Web") or {}).get(f"{host}:443", {}).get("Handlers") or {}).get("/", {}).get("Proxy")
    if proxy and proxy != target:
        return None, f"Tailscale already sends {url} to {proxy}, so it was left as it is."
    if proxy == target and (serve.get("AllowFunnel") or {}).get(f"{host}:443"):
        return url, None
    try:
        done = run([ts, "funnel", "--bg", str(port)], capture_output=True, text=True, timeout=20)
        ok, said = done.returncode == 0, (done.stdout or "") + (done.stderr or "")
    except subprocess.TimeoutExpired as e:   # Funnel not yet allowed on the tailnet: it prints a link and waits
        ok, said = False, (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
    if ok:
        return url, None
    return None, "Tailscale Funnel did not turn on:\n    " + "\n    ".join(said.strip().splitlines() or ["(no output)"])


def plist(config: Path, path: str, env=os.environ) -> bytes:
    """The LaunchAgent: `python -m gobi_a2a serve -c <config>`, with PATH and the variables the config names."""
    names = {m.group(1) for m in ENV_REF.finditer(config.read_text())}
    variables = {"PATH": path, **{n: env[n] for n in sorted(names) if env.get(n)}}
    log = str(home() / "gateway.log")
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-m", "gobi_a2a", "serve", "-c", str(config)],
        "EnvironmentVariables": variables,
        "WorkingDirectory": str(config.parent),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},     # back up after a crash, not after a clean stop
        "ThrottleInterval": 30,
        "StandardOutPath": log,
        "StandardErrorPath": log,
    })


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _unload() -> None:
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    for _ in range(50):                               # bootout returns before the job is gone
        if _launchctl("print", f"gui/{os.getuid()}/{LABEL}").returncode != 0:
            return
        time.sleep(0.1)


def _answers(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _healthy(host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def _tail(p: Path, n: int = 15) -> str:
    try:
        return "\n".join(p.read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return "(no log)"


def start(config: Path, *, load: Callable[[Path], Config], describe: Callable[[Config, bool], str]) -> None:
    if sys.platform != "darwin":
        sys.exit("gobi-a2a start runs the gateway as a macOS LaunchAgent. Elsewhere, run `gobi-a2a serve` under your "
                 "service manager (systemd, ...).")
    path = search_path()
    notes: list[str] = []
    if "/archive-v" in sys.prefix:                     # uvx's throwaway environment, which `uv cache clean` removes
        notes.append("This runs from uvx's cache, which uv may clean. To keep it: uv tool install "
                     "git+https://github.com/gobi-ai/gobi-a2a, then gobi-a2a start.")
    new = not config.exists()
    cfg = None if new else load(config)
    port = cfg.gateway.port if cfg else 8000
    url = None
    public = cfg.gateway.public_url if cfg else None
    if public is None or public.endswith(".ts.net"):   # another tunnel's address is left to that tunnel
        url, note = funnel(port, path)
        notes += [note] if note else []
    here = found(path, claude=claude_signed_in())
    if new:
        write_config(config, here, url, port)
        cfg = load(config)
    elif public is None and url:                       # Tailscale came after the first start
        if fill_public_url(config, url):
            cfg = load(config)
        else:
            notes.append(f"Tailscale Funnel serves {url}: set gateway.public_url to it in {config}, so Agent Cards "
                         "name it.")
    assert cfg is not None
    for name, ok in here.items():
        if ok and not cfg.agents[name].enabled:
            notes.append(f"{name} is here but disabled: set agents.{name}.enabled: true in {config} to offer it.")
        elif not ok and cfg.agents[name].enabled:
            notes.append(f"{name} is enabled but not found ({INSTALL[name]}): its tasks will fail.")

    host = cfg.gateway.host if cfg.gateway.host not in ("0.0.0.0", "::", "") else "127.0.0.1"
    _unload()
    if _answers(host, port):
        sys.exit(f"gobi-a2a: something else listens on port {port} (a gobi-a2a started by hand?). Stop it and run "
                 "`gobi-a2a start` again, or set gateway.port in " + str(config))
    home().mkdir(parents=True, exist_ok=True)
    pl = plist_path()
    pl.parent.mkdir(parents=True, exist_ok=True)
    pl.write_bytes(plist(config, path))
    pl.chmod(0o600)                                   # it may carry the token
    done = _launchctl("bootstrap", f"gui/{os.getuid()}", str(pl))
    if done.returncode != 0:
        sys.exit(f"gobi-a2a: launchctl could not start it: {done.stderr.strip()}")
    log = home() / "gateway.log"
    for _ in range(60):
        if _healthy(host, port):
            break
        time.sleep(0.5)
    else:
        sys.exit(f"gobi-a2a: the gateway did not come up in 30s. The end of {log}:\n{_tail(log)}")

    print(f"gobi-a2a is running, and starts again when you log in.\n  config  {config}\n  log     {log}\n")
    print(describe(cfg, True))
    for note in notes:
        print(f"\n  ! {note}")


def stop() -> None:
    pl = plist_path()
    if not pl.exists():
        print("gobi-a2a is not set up to run in the background.")
        return
    _unload()
    pl.unlink()
    print("gobi-a2a stopped, and does not start at login. `gobi-a2a start` starts it again.")
