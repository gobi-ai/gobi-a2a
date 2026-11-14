# gobi-a2a

One gateway in front of several coding agents, each an [A2A](https://a2a-protocol.org) server a
Gobi harness (`gobi-harness`) can hand work to. One process, one port, one tunnel, one config file (`a2a.yaml`). Each
agent is mounted under its own name:

| Agent | Path | What it runs | State |
|---|---|---|---|
| `claude` | `/claude` | Claude Code, through the Claude Agent SDK | **on by default**, complete (from `gobi-harness/a2a/claude`) |
| `cursor` | `/cursor` | Cursor's agent CLI (`cursor-agent --print`) | scaffold, off by default |
| `aider` | `/aider` | Aider (`aider --message`) | scaffold, off by default |
| `omp` | `/omp` | omp, oh-my-pi (`omp -p`) | scaffold, off by default |

The Gobi app takes one **address** per worker. With the gateway, each agent's address is `https://<your-domain>/<agent>`,
for example `https://you.ngrok.app/claude`. All agents can sit behind the same tunnel and token.

## Quick start

```sh
git clone <this repo> gobi-a2a && cd gobi-a2a
uv sync                                   # or: uv venv && uv pip install -e .
cp a2a.example.yaml a2a.yaml              # a2a.yaml is git-ignored: it is yours
export A2A_TOKEN=$(.venv/bin/gobi-a2a token)    # Gobi presents this; keep it
export A2A_PUBLIC_URL=https://<your-domain>     # the tunnel's URL, below
.venv/bin/gobi-a2a                        # listens on 127.0.0.1:8000

ngrok http --url=<your-domain> 8000       # in another terminal; or a Cloudflare Tunnel
```

Then, in the Gobi app under **Workers**, add `https://<your-domain>/claude` and the token. Do the same for each other
agent you enable (`/cursor`, `/aider`, `/omp`), with the same token unless you gave that agent its own.

Claude Code signs in as it does on this machine (`ANTHROPIC_API_KEY`, or its own login), and the run's cost is on that
account. The CLI agents sign in the same way their programs do on this machine.

## The command

```sh
gobi-a2a [serve] [-c a2a.yaml] [--host H] [--port P]   # start the gateway (serve is the default)
gobi-a2a check   [-c a2a.yaml]                         # check the config; print each agent's address and settings
gobi-a2a token                                         # print a new random bearer token
python -m gobi_a2a ...                                 # the same
```

The config is `-c/--config`, else `$GOBI_A2A_CONFIG`, else `./a2a.yaml`. A mistake in it (an unknown key or agent, a
bad value) stops the gateway at start and names the key, so a typo cannot pass silently.

## Configuration: `a2a.yaml`

[`a2a.example.yaml`](a2a.example.yaml) is the full, commented reference. In short:

```yaml
gateway:
  host: 127.0.0.1
  port: 8000
  public_url: ${A2A_PUBLIC_URL}   # what the Agent Cards name; unset: http://host:port
  token: ${A2A_TOKEN}             # the bearer token callers present
  no_auth: false                  # true: no token at all. Local testing only, never behind a tunnel
  workdir: ./work                 # <workdir>/<agent>/<conversation>/
  log_level: info

agents:
  claude:
    enabled: true
    model: claude-opus-5-5
    permission_mode: default
  cursor:
    enabled: false
```

- **Environment references.** Any string may say `${NAME}` or `${NAME:-fallback}`. Keep the token in the environment
  (or a secret manager) rather than in the file. An unset variable is empty, and an empty value means the same as
  leaving the key out, so the default applies.
- **Paths.** Relative paths start at the config file's folder; `~` works.
- **Per-agent fields every agent has.** `enabled`, `name` and `description` (what its Agent Card says; the harness
  shows the description to its super agent), `token` (its own bearer token in place of the gateway's), and `workdir`
  (in place of `<gateway.workdir>/<agent>`).

### `claude`

| Key | Default | |
|---|---|---|
| `model` | `claude-opus-5-5` | a message may pick another (below) |
| `effort` | none (Claude Code's default) | `low`, `medium`, `high`, `xhigh` or `max`; a message may pick another |
| `thinking` | none (Claude Code's default, adaptive on Opus) | `adaptive`, `disabled`, or a token budget such as `16000` |
| `allowed_tools` | `[Read, Glob, Grep, Write, Edit, WebSearch, WebFetch]` | the tools that run without asking; others are refused. **No Bash** |
| `permission_mode` | `default` | `default`, `acceptEdits`, `plan`, `bypassPermissions`, `dontAsk` or `auto` |
| `max_turns` | none (**no limit**) | an optional cap on turns per run |
| `system_prompt` | none | added to Claude Code's own |

Nobody is there to approve a tool, so only `allowed_tools` run. With `permission_mode: bypassPermissions`, **every tool
runs, Bash included, whatever `allowed_tools` says.** Use it only where Claude may do anything the account running the
gateway can, such as a container or a dedicated machine. To switch it, edit `a2a.yaml` and restart the gateway
(Ctrl-C, then `gobi-a2a`). The tunnel can stay up.

### `cursor`, `aider`, `omp` (scaffolds)

Each runs its command line program once per task, in that conversation's folder. Each line the program prints is a
progress note, and what it printed is the result when it exits 0. Any other exit fails the task with the end of its
stderr. A cancel stops the process (SIGTERM, then SIGKILL).

| Key | |
|---|---|
| `command` | argv list, never a shell line. `"{prompt}"` is replaced by the task's text; without one, the text is appended as the last argument |
| `model`, `model_args` | the default model, and the flags that pass one (`[--model, "{model}"]`), added only when a model is set here or by the message |
| `effort`, `effort_args` | the same for effort (omp: `[--thinking, "{effort}"]`) |
| `timeout` | seconds per run; empty: no limit |
| `env` | variables added for the run, e.g. `CURSOR_API_KEY: ${CURSOR_API_KEY}` |

The defaults:

- **cursor:** `cursor-agent --print --output-format text <prompt>`. In print mode Cursor may refuse to change files or
  run commands. Add `--force` to `command` where that is safe; it is Cursor's counterpart to `bypassPermissions`.
- **aider:** `aider --yes-always --no-pretty --no-stream --no-check-update --message <prompt>`. Aider keeps a git repo
  and its chat history in the folder. Add `--restore-chat-history` to carry a conversation on.
- **omp:** `omp -p <prompt>`. The flags follow the pi coding agent's CLI. Check `omp --help` and adjust `command`,
  `model_args` and `effort_args` if your version differs (pi's thinking levels stop at `xhigh`).

The gateway starts even if a program is not installed. It logs a warning, and that agent's tasks fail with
"not installed".

## How Gobi Harness connects

The harness (`gobi-harness/engine/a2a.py`) is given an address and nothing else:

1. It reads the **Agent Card** at `<address>/.well-known/agent-card.json`, for example
   `https://you.ngrok.app/claude/.well-known/agent-card.json`. Cards are **public**, so the harness can read one before
   it has a credential. Each card declares HTTP Bearer, so the app asks for the token.
2. It sends work to the JSON-RPC URL the card names. The gateway always names `<public_url>/<agent>/` (with the
   trailing slash, because the harness does not follow redirects). Each brief is one A2A message (protocol 1.0,
   `SendMessage` with `returnImmediately`). The harness then polls the task with `GetTask`, sends steers to the same
   task, and calls `CancelTask` on kill.
3. The final status message's metadata carries the run's cost (`cost_usd`, `usage`, `turns`, `model`, `effort`), and
   the harness adds it to the worker's use.

`public_url` must therefore be the URL the harness actually reaches, meaning your tunnel's `https://` URL. The harness
only talks to public https addresses. If the tunnel URL changes (a free ngrok URL changes on each restart), update
`A2A_PUBLIC_URL`, restart, and edit the worker's address in the app.

## Model and effort per task

`model` and `effort` in `a2a.yaml` are defaults. A message can name its own for the run it starts, in its metadata.
The request's metadata works too, and the message's wins:

```json
{"message": {"role": "ROLE_USER", "parts": [{"text": "..."}], "metadata": {"model": "claude-sonnet-5", "effort": "low"}}}
```

A follow-up in the same context, or a steer sent to the task itself, resumes the same Claude session on whatever it
names. Anything it leaves out falls back to the server's default, not to what the previous run used. The Gobi harness
sends the ticket's current choice with every message. A model that is not a model name, or an effort other than `low`,
`medium`, `high`, `xhigh` or `max`, rejects the task before anything runs. The final status message says which model
and effort ran. The CLI scaffolds take the same `model` and `effort` metadata and pass it through
`model_args`/`effort_args`.

## Endpoints

| | Auth | |
|---|---|---|
| `GET /healthz` | public | `{"status": "ok"}` |
| `GET /` | gateway token | the gateway, its enabled agents (address, endpoint, card URL, auth) and which are disabled |
| `GET /v1/agents` | gateway token | the enabled agents, each with its full Agent Card |
| `GET /<agent>/.well-known/agent-card.json` | public | the agent's card |
| `POST /<agent>/` | the agent's token | A2A JSON-RPC: `SendMessage`, `SendStreamingMessage`, `GetTask`, `CancelTask`, ... |

Tokens are compared in constant time. A wrong or missing one gets `401` with `WWW-Authenticate: Bearer`.

```sh
curl -s -H "Authorization: Bearer $A2A_TOKEN" localhost:8000/ | python3 -m json.tool
```

## Layout

```
gobi_a2a/
  main.py          the gobi-a2a command (argparse -> config -> uvicorn)
  config.py        a2a.yaml: loading, ${ENV} references, validation
  gateway.py       the FastAPI app: discovery endpoints, mounts each enabled agent at /<name>
  auth.py          the bearer-token ASGI middleware
  agents/
    __init__.py    AGENTS: name -> module
    common.py      shared: AgentConfig, Agent Card, per-run model/effort, the A2A app an agent is
    claude.py      Claude Code (executor, config, build)
    cli.py         the command line agent executor the scaffolds share
    cursor.py, aider.py, omp.py
tests/             pytest; Claude Code is a scripted run, the CLIs are tests/fake_agent.py
```

**Adding an agent:** write `gobi_a2a/agents/<name>.py` with a `Config` (subclass `AgentConfig`, or `CLIConfig` for a
command line program) and a `build(name, cfg, *, url, workdir, auth, token) -> Mounted`, register it in `AGENTS`, add
its block to `a2a.example.yaml`, and add tests. A CLI agent is only a `Config` with a default `command` plus a call to
`build_cli`; see `cursor.py`. To go beyond one-run-per-task (structured events, resumable sessions), write an
`AgentExecutor` as `claude.py` does.

## Tests

```sh
uv sync --extra dev && .venv/bin/pytest
```

They replace Claude Code with a scripted run and the CLIs with `tests/fake_agent.py`, so they cost nothing and need none
of the agents installed.

## Limits

- Tasks and Claude sessions live in memory, so a restart forgets them. The folders stay.
- A message sent to a task that is still running waits until that run ends. To continue, send a new task in the same
  context.
- Text in, text out. Files an agent writes stay in its folder.
- The CLI scaffolds have no session of their own: a follow-up works in the same folder, and the program decides what it
  remembers from there.
