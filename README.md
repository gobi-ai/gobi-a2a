# gobi-a2a

[![A2A 1.0](https://img.shields.io/badge/A2A-1.0-6f42c1)](https://a2a-protocol.org)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776ab?logo=python&logoColor=white)](pyproject.toml)
[![JSON-RPC 2.0](https://img.shields.io/badge/transport-JSON--RPC%202.0-555)](docs/protocol.md#3-transport-http-headers-json-rpc-envelope)
[![Gobi](https://img.shields.io/badge/works%20with-Gobi-ff7a1a)](docs/connecting-gobi-app.md)

**Unified gateway serving local coding agents over the official [A2A protocol](https://a2a-protocol.org).**

Run Claude Code, Cursor, Codex, Oh My Pi or GitHub Copilot CLI on your own machine and hand them work from the Gobi
app, or from any A2A client. One process, one port, one tunnel, one config file (`a2a.yaml`).

## Supported agents

| Agent | Path | Runs | Status |
|---|---|---|---|
| **Claude Code** | `/claude` | the Claude Agent SDK | ✅ on by default |
| **Cursor** | `/cursor` | `cursor-agent --print`, resumes its chat | 🧪 scaffold, off by default |
| **Codex** | `/codex` | `codex exec`, resumes its session | 🧪 scaffold, off by default |
| **Oh My Pi** | `/omp` | `omp --mode json`, resumes its session | 🧪 scaffold, off by default |
| **GitHub Copilot CLI** | `/copilot` | `copilot -p --autopilot`, resumes its session | 🧪 scaffold, off by default |

Each agent gets its own address, `https://<your-domain>/<agent>`, behind the same tunnel and token.

## 30-Second Quick Start

**On a Mac, two lines.** [uv](https://docs.astral.sh/uv/) brings its own Python; [Tailscale](https://tailscale.com/download)
(signed in) gives the public address.

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv tool install git+https://github.com/gobi-ai/gobi-a2a && ~/.local/bin/gobi-a2a start
```

`gobi-a2a start` writes `~/.gobi-a2a/a2a.yaml` the first time, with a new token and only the agents it finds on this
Mac enabled (Claude Code when you are signed in to it; the others when their command is installed). It turns on
Tailscale Funnel, runs the gateway in the background (again at each login), and prints each agent's address and the
token. `gobi-a2a stop` stops it; run `start` again after editing the config.

**Connect in Gobi App:** **Workers** ▸ gear ▸ **Remote agent**, paste the address and the token. Done.

<details>
<summary>By hand, from a clone (any OS)</summary>

```sh
git clone https://github.com/gobi-ai/gobi-a2a && cd gobi-a2a
uv sync
cp a2a.example.yaml a2a.yaml
export A2A_TOKEN=$(.venv/bin/gobi-a2a token)     # the token Gobi will present; keep it
tailscale funnel --bg 8000                       # recommended: a permanent https://<machine>.<tailnet>.ts.net
# or
cloudflared tunnel --url http://localhost:8000   # free, no account; a new random address on every start
export A2A_PUBLIC_URL=https://<machine>.<tailnet>.ts.net
.venv/bin/gobi-a2a
```

It ends its start-up output with an **Agent URLs ready** block: each agent's address, and the token, masked.

</details>

> Gobi only calls public `https://` addresses. Tailscale Funnel's address never changes. A Cloudflare *quick tunnel*
> changes on each start and does not carry streaming; fine for Gobi, which polls. Details and other tunnels:
> [choosing a tunnel](docs/connecting-gobi-app.md#a-public-https-address-choosing-a-tunnel).

## Connecting to Gobi App

Add each agent to your Gobi personal agent as a **Remote agent** worker. You need two values, both from the terminal:

| | Value | Example |
|---|---|---|
| **A · Address** | `<A2A_PUBLIC_URL>/<agent>`, printed under *Agent URLs ready* | `https://my-mac.tail1234.ts.net/claude` |
| **B · Token** | `$A2A_TOKEN` (`printf %s "$A2A_TOKEN" \| pbcopy` on macOS) | 48 hex characters |

<p align="center"><img src="docs/images/terminal-to-app.svg" alt="Copy the address (A) and the token (B) from the terminal into the Gobi app's Remote agent screen" width="100%"></p>

In the app: **Personal agent** ▸ title pill ▸ **Workers** ▸ gear ▸ **Remote agent** ▸ paste the **Address** ▸
**Look it up** ▸ paste the **Token** ▸ **Add**. Then ask for it by name: *"Have claude-code fix the flaky upload
test in ~/src/app"*.

📱 **[Full screen-by-screen walkthrough →](docs/connecting-gobi-app.md)**

## Architecture

<p align="center"><img src="docs/images/connection-flow.svg" alt="Gobi app to Gobi harness to the https tunnel to gobi-a2a to Claude Code" width="100%"></p>

```
Gobi app ─▶ Gobi harness (cloud) ─▶ https tunnel ─▶ gobi-a2a :8000 ─┬─▶ /claude  Claude Code (Agent SDK)
                                                                    ├─▶ /cursor  cursor-agent
                                                                    ├─▶ /codex   codex exec
                                                                    ├─▶ /omp     omp --mode json
                                                                    └─▶ /copilot copilot -p
```

The harness reads each agent's public Agent Card at `/<agent>/.well-known/agent-card.json`, sends work with
`SendMessage`, polls with `GetTask`, and stops it with `CancelTask`, all A2A 1.0 over JSON-RPC 2.0 with a bearer
token. Nothing is Gobi-specific: any A2A client can call these agents, and Gobi can call any A2A agent.
[`examples/echo_agent.py`](examples/echo_agent.py) is a complete third-party agent in about 100 lines.

## Docs

| | |
|---|---|
| 📱 [Connecting to Gobi App](docs/connecting-gobi-app.md) | the 8-step, screen-by-screen walkthrough, and how the harness connects |
| ⚙️ [Configuration](docs/configuration.md) | the `gobi-a2a` command, `a2a.yaml`, every agent's keys, per-task model and effort, endpoints |
| 🩺 [Troubleshooting](docs/troubleshooting.md) | what to check when it does not connect, recovering after a restart, known limits |
| 📡 [A2A protocol reference](docs/protocol.md) | every method, request and response, the task lifecycle, building your own agent |

## Development

```sh
uv sync --extra dev && .venv/bin/pytest
```

The tests replace Claude Code with a scripted run and the CLIs with `tests/fake_agent.py`, so they cost nothing and
need none of the agents installed. `tests/test_protocol.py` sends raw JSON-RPC exactly as
[docs/protocol.md](docs/protocol.md) documents it, so a change that would make the reference wrong fails a test.

```
gobi_a2a/
  main.py        the gobi-a2a command          config.py   a2a.yaml loading and validation
  service.py     gobi-a2a start / stop: first-run config, Tailscale Funnel, the macOS LaunchAgent
  gateway.py     FastAPI app, mounts agents    auth.py     bearer-token middleware
  agents/        common.py, claude.py, cli.py (shared CLI executor), cursor.py, codex.py, omp.py, copilot.py
tests/           pytest, including test_protocol.py
examples/        echo_agent.py, a standalone A2A agent
```

**Adding an agent:** write `gobi_a2a/agents/<name>.py` with a `Config` (subclass `AgentConfig`, or `CLIConfig` for a
command line program) and a `build(name, cfg, *, url, workdir, auth, token) -> Mounted`, register it in `AGENTS`, add
its block to `a2a.example.yaml`, and add tests. A CLI agent is only a `Config` with a default `command` plus a call to
`build_cli`; see `cursor.py`. To go beyond one-run-per-task (structured events, resumable sessions), write an
`AgentExecutor` as `claude.py` does.
