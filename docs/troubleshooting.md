# Troubleshooting

What to check when the Gobi app cannot reach an agent, how to recover after a restart, and the gateway's known limits.
Setting it up from scratch is in [connecting-gobi-app.md](connecting-gobi-app.md).

## First, check the card through the tunnel

Every connection problem starts here. If this fails, the app's **Look it up** fails the same way:

```console
~/gobi-a2a $ curl -s https://my-mac.tail1234.ts.net/claude/.well-known/agent-card.json | python3 -m json.tool | head -3
{
    "name": "Claude Code",
    "description": "Claude Code working in its own folder: code, files and web research.",
```

`gobi-a2a check` prints the configuration and the *Agent URLs ready* block without starting anything, so you can
compare the addresses the gateway thinks it has with the one you entered in the app.

## If it does not connect

| What you see | Why, and what to do |
|---|---|
| *Could not read the agent's card* after **Look it up** | The app could not fetch `<address>/.well-known/agent-card.json`. Run the `curl` above. Check that the gateway is running, the tunnel is up (`tailscale funnel status`), the address ends in the agent's path, and that agent is `enabled: true`. `tailscale serve` is tailnet-only: Gobi needs `tailscale funnel`. |
| The card names the wrong address | `A2A_PUBLIC_URL` was not set, or was set to another address, when the gateway started. The card tells Gobi where to send work, so set it to the tunnel's `https://` URL and restart the gateway. |
| No **Token** field | The gateway runs with `no_auth: true`. Never expose that through a tunnel: set a token and restart. |
| Tasks fail, or the worker shows **CHECK** | Open the worker in Worker settings: the page says what needs attention. A `401` means the token Gobi holds is not the one the gateway expects (the gateway's `token`, or the agent's own `token:` in `a2a.yaml`). Tap **Enter it again** and paste the current one. |
| It worked, then stopped after a restart | With a Cloudflare quick tunnel the address changed: update `A2A_PUBLIC_URL`, restart the gateway, then remove the worker and add it again. A Tailscale Funnel address does not change. |
| Start-up ends with `! http://127.0.0.1:8000 is not https` | `A2A_PUBLIC_URL` is not set, so the Agent URLs block lists local addresses Gobi cannot call. Set it to the tunnel's address and restart. |
| The gateway refuses to start and names a key | A typo or bad value in `a2a.yaml` (an unknown key or agent). Fix the key it names; see [configuration.md](configuration.md). |
| A CLI agent's tasks fail with "not installed" | Its program (`cursor-agent`, `aider`, `omp`) is not on `PATH` for the gateway. Install it, or point `command` at it. |
| `SendStreamingMessage` hangs through the tunnel | Cloudflare quick tunnels do not carry Server-Sent Events. Poll with `GetTask` (the Gobi harness only polls), or use a Tailscale Funnel or a named Cloudflare tunnel. |
| A pre-1.0 call (`tasks/send`, `message/send`) gets `-32601` | Only A2A 1.0 method names are served. See [protocol.md §7.8](protocol.md#78-method-names-from-earlier-versions-taskssend-tasksget-). |
| Every call fails with `-32009` | The `A2A-Version: 1.0` header is missing. See [protocol.md §3](protocol.md#3-transport-http-headers-json-rpc-envelope). |

## Recovering after a restart or failure

- **Gateway restarted.** Tasks and Claude sessions live in memory, so a restart forgets them; tasks that were running
  are gone. The conversation folders under `workdir` stay, so a new task in the same folder can pick up the files.
  Ask again in Gobi.
- **Tunnel address changed** (Cloudflare quick tunnel). The app's worker address cannot be edited: update
  `A2A_PUBLIC_URL`, restart the gateway, then in **Worker settings** remove the worker and add it again with the new
  address. Tailscale Funnel avoids this, because its address never changes.
- **Token rotated.** Make a new one with `gobi-a2a token`, restart the gateway with it, then in **Worker settings**
  open each worker and tap **Enter it again**.
- **Agent renamed or re-described** in `a2a.yaml`. Restart, then tap **Read its card again** on the worker's page.
- **A run went the wrong way.** A steer is queued, not acted on mid-run. To change course at once, stop the task
  (tap it on **Workers** ▸ **Stop**, i.e. `CancelTask`) and send a new task in the same context.

## Known limits

- Tasks and Claude sessions live in memory, so a restart forgets them. The folders stay.
- A steer to a running task is queued, not acted on mid-run: it runs when the current run ends. To change course
  at once, cancel and send a new task in the same context.
- **Known bug, inherited from the original `gobi-harness/a2a/claude`:** if a steer is queued when `CancelTask` arrives,
  the cancel answers `CANCELED`, but the steer still runs afterwards and the task ends `COMPLETED`. Work, and cost,
  happen after a cancel. See [protocol.md §13](protocol.md#13-known-deviations-and-limits); an `xfail` test tracks it.
- Text in, text out. Files an agent writes stay in its folder.
- The CLI scaffolds have no session of their own: a follow-up works in the same folder, and the program decides what it
  remembers from there.
