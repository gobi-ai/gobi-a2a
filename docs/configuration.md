# Configuration reference

Everything that controls `gobi-a2a`: the command, `a2a.yaml`, each agent's keys, per-task model and effort, and the
HTTP endpoints. For the wire protocol itself see [protocol.md](protocol.md).

**Contents**

- [The command](#the-command)
- [`a2a.yaml`](#a2ayaml)
  - [`claude`](#claude)
  - [`cursor`, `codex`, `omp`, `copilot` (scaffolds)](#cursor-codex-omp-copilot-scaffolds)
- [Model and effort per task](#model-and-effort-per-task)
- [Endpoints](#endpoints)

## The command

```sh
gobi-a2a [serve] [-c a2a.yaml] [--host H] [--port P]   # start the gateway (serve is the default)
gobi-a2a check   [-c a2a.yaml]                         # check the config; print each agent's address, settings, and what to enter in Gobi
gobi-a2a token                                         # print a new random bearer token
python -m gobi_a2a ...                                 # the same
```

The config is `-c/--config`, else `$GOBI_A2A_CONFIG`, else `./a2a.yaml`. A mistake in it (an unknown key or agent, a
bad value) stops the gateway at start and names the key, so a typo cannot pass silently.

## `a2a.yaml`

[`a2a.example.yaml`](../a2a.example.yaml) is the full, commented reference. In short:

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
| `model` | `claude-opus-5-5` | a message may pick another ([below](#model-and-effort-per-task)) |
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

Claude Code signs in as it does on this machine (`ANTHROPIC_API_KEY`, or its own login), and the run's cost is on that
account.

### `cursor`, `codex`, `omp`, `copilot` (scaffolds)

Each runs its command line program once per task, in that conversation's folder. Each line the program prints is a
progress note, and what it printed is the result when it exits 0. Any other exit fails the task with the end of its
stderr. A cancel stops the process (SIGTERM, then SIGKILL).

| Key | |
|---|---|
| `command` | argv list, never a shell line. `"{prompt}"` is replaced by the task's text; without one, the text is appended as the last argument. Model and effort flags go just before a `--` in it, if it has one |
| `model`, `model_args` | the default model, and the flags that pass one (`[--model, "{model}"]`), added only when a model is set here or by the message |
| `effort`, `effort_args` | the same for effort (omp: `[--thinking, "{effort}"]`, codex: `[-c, "model_reasoning_effort={effort}"]`, copilot: `["--reasoning-effort={effort}"]`) |
| `resume_args` | the flags that continue a session (codex: `[resume, "{session}"]`, cursor and omp: `[--resume, "{session}"]`), added for a follow-up in the same conversation; `[]` starts each task afresh. copilot's, `["--session-id={session}"]`, go on every task: the gateway names the session |
| `timeout` | seconds per run; empty: no limit |
| `env` | variables added for the run, e.g. `CURSOR_API_KEY: ${CURSOR_API_KEY}` |

The defaults:

- **cursor:** `cursor-agent --print --output-format stream-json --trust -- <prompt>`. Its JSON events become progress
  notes and the result, and its session id is kept so a follow-up resumes the chat. `--trust` is needed because each
  conversation has a new folder. Cursor may still refuse to change files or run commands. Add `--force` to `command` where that is safe; it is Cursor's counterpart to `bypassPermissions`.
- **codex:** `codex exec --json --skip-git-repo-check --sandbox workspace-write --color never -- <prompt>`. Its JSON
  events become progress notes (commands, file changes) and the result (the last message), and its session id is kept
  so a follow-up runs `codex exec resume <session>`. The sandbox lets Codex change files and run commands in the
  conversation's folder, without network; `danger-full-access` lifts it. Codex signs in with `codex login` or `CODEX_API_KEY`.
- **omp:** `omp --mode json -- <prompt>`. Its JSON events become progress notes (tool calls) and the result (the last
  assistant text), and the session id from its first line is kept so a follow-up runs with `--resume <session>`. omp
  signs in with `omp login` (a ChatGPT, Copilot or Gemini subscription, or an API key) and needs bun. Its thinking
  levels stop at `xhigh`.
- **copilot:** `copilot --prompt=<prompt> --silent --no-color --allow-all-tools --allow-all-urls --no-ask-user
  --autopilot`, GitHub Copilot CLI (`npm install -g @github/copilot` or `brew install copilot-cli`). `--silent` prints
  only the answer, which is the result. Nobody is there to approve anything, and without permission flags `-p` denies
  every request that needs it, so `--allow-all-tools` lets Copilot run its tools (shell, file writes) in the
  conversation's folder, `--allow-all-urls` lets it fetch from the web, `--no-ask-user` keeps it from stopping to ask,
  and `--autopilot` lets it keep going until the task is done (Copilot has no `--yes`). `--allow-all` (or `--yolo`) also
  lifts its path check, Copilot's counterpart to `bypassPermissions`. Copilot does not print a session id here but
  takes one: the conversation's first task runs with a new UUID in `--session-id`, which starts that session, and each
  follow-up with the same one, after a gateway restart too. A failed run keeps the session, since the id stays good.
  Model and effort: `--model=<name>` (`claude-sonnet-4.5`, `gpt-5`, `auto`, ...) and `--reasoning-effort=<level>`, from
  `a2a.yaml` or the message ([below](#model-and-effort-per-task)). Copilot signs in with
  `copilot login`, or `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` in `env` (a fine-grained token with the *Copilot Requests*
  permission; classic `ghp_` tokens do not work). The `copilot` that VS Code's Copilot Chat puts on `PATH` is a shim,
  not the CLI. It asks to install the CLI and exits 0 even when a run fails, so the gateway leaves its folder off the
  run's `PATH`. Without the real CLI, tasks fail with "not installed".

The CLI agents sign in the same way their programs do on this machine. The gateway starts even if a program is not
installed. It logs a warning, and that agent's tasks fail with "not installed".

## Model and effort per task

`model` and `effort` in `a2a.yaml` are defaults. A message can name its own for the run it starts, in its metadata.
The request's metadata works too, and the message's wins:

```json
{"message": {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": "..."}],
             "metadata": {"model": "claude-sonnet-5", "effort": "low"}}}
```

A follow-up in the same context, or a steer sent to the task itself, resumes the same Claude session on whatever it
names. Anything it leaves out falls back to the server's default, not to what the previous run used. The Gobi harness
sends the ticket's current choice with every message. A model that is not a model name, or an effort other than `low`,
`medium`, `high`, `xhigh` or `max`, rejects the task before anything runs. The final status message says which model
and effort ran. The CLI scaffolds take the same `model` and `effort` metadata (`reasoning_effort` works as another
name for `effort`) and pass it through `model_args`/`effort_args`, e.g. copilot's
`--model=claude-sonnet-4.5 --reasoning-effort=high`.

**Sessions (CLI scaffolds).** Each conversation's session is kept in `<workdir>/<agent>/.sessions/`, so a follow-up
resumes it after a gateway restart as well. A message may pick it, in the same metadata: `"session": "<id>"` resumes
that session (an exact id: one the agent listed, or one a task's final status metadata reported) and keeps it for the
follow-ups; `"session": "new"` starts afresh. Anything else that is not a session id rejects the task. The final status
message's metadata says which session ran (`"session"`).

Tasks in one conversation take turns. A follow-up or a steer sent as a new task while one is running waits for it,
saying so, then resumes the session the run before left, rather than racing it in the same folder and session. A
message sent to the running task itself (its `taskId`) is held by the A2A server until that run ends, and the task
has ended by then, so it does not run: steer with a new task in the same conversation (its `contextId`).

## Endpoints

| | Auth | |
|---|---|---|
| `GET /healthz` | public | `{"status": "ok"}` |
| `GET /` | gateway token | the gateway, its enabled agents (address, endpoint, card URL, auth) and which are disabled |
| `GET /v1/agents` | gateway token | the enabled agents, each with its full Agent Card |
| `GET /<agent>/.well-known/agent-card.json` | public | the agent's card |
| `POST /<agent>/` | the agent's token | A2A JSON-RPC: `SendMessage`, `SendStreamingMessage`, `GetTask`, `CancelTask`, ... |

Tokens are compared in constant time. A wrong or missing one gets `401` with `WWW-Authenticate: Bearer`. A2A calls
also need the header `A2A-Version: 1.0`. Each method's request and response is in
[protocol.md §7](protocol.md#7-methods), with a curl cookbook in [§14](protocol.md#14-curl-cookbook).

```sh
curl -s -H "Authorization: Bearer $A2A_TOKEN" localhost:8000/ | python3 -m json.tool
```
