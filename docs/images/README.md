# docs/images

The pictures in [Connecting to Gobi App](../connecting-gobi-app.md) (two of them also appear in the main
[README](../../README.md)).

The terminal images show `gobi-a2a`'s real output (only the token and paths are examples). The phone screens are
**wireframes**, not screenshots. They follow the Gobi app's Workers pages (gobi-app's
`lib/scenes/main/explore/agent_workers_page.dart` and `agent_worker_widgets.dart`), with the same labels, order and
pieces: the stat tiles, each worker's avatar, the tone pills and the notices. Orange numbered outlines mark what to tap, and the numbers match the walkthrough's steps.

| File | Screen | Walkthrough step |
|---|---|---|
| `connection-flow.svg` | Diagram: Gobi app → Gobi harness → https tunnel → gobi-a2a → Claude Code | overview |
| `terminal-session.svg` | Terminal: `tailscale funnel --bg 8000`, then `gobi-a2a` and its *Agent URLs ready* block | on your computer |
| `terminal-to-app.svg` | The address (A) and token (B) copied from the terminal into **Remote agent** | terminal to app |
| `01-personal-agent-menu.svg` | Personal agent chat, the title pill's menu open | 1–2: title pill ▸ **Workers** |
| `02-workers.svg` | **Workers**: Running, Waiting and Done today at 0, *Nothing running* | 3: gear ▸ **Worker settings** |
| `03-worker-settings.svg` | **Worker settings**: nothing under Yours, Gobi's own under From Gobi | 4: **Remote agent** |
| `04-remote-agent-address.svg` | **Remote agent**, Address filled in | 5–6: Address ▸ **Look it up** |
| `05-remote-agent-token.svg` | **Remote agent** after the look-up: the card and its skills, the notice, Name, Token | 7–8: Token ▸ **Add** |
| `06-worker-added.svg` | **Worker settings** with `claude-code` under Yours, its pills, and the *Added* toast | result |
| `07-workers-running.svg` | **Workers** with a running task (its progress line and ⊗) and a recently finished one | using it |

## Replacing a wireframe with a screenshot

1. Take the screenshot on a phone (light mode reads best on GitHub). Cover anything private: the token field is
   masked, but your agent's name, tasks, and a tailnet address can show.
2. Crop it to the screen, and keep it under about 300 KB (`pngquant` or `sips -Z 1200` helps).
3. Save it here with the **same name** and a `.png` extension, for example `03-worker-settings.png`.
4. In `../connecting-gobi-app.md` (and `../../README.md`, for the two it shows), change that
   `<img src="images/03-worker-settings.svg" ...>` to `.png`, and delete the `.svg`.

Keep the `width="240"` on phone screens so the three-across rows still fit. If the app's labels change, update the
walkthrough's steps and this table at the same time.

## Redrawing the wireframes

`wireframes.py` draws `02`–`07` and the phone inside `terminal-to-app.svg`, from the app's labels and layout. After the
app's Workers pages change, edit it to match and run `python3 docs/images/wireframes.py docs/images` (no
dependencies). `01-personal-agent-menu.svg`, `connection-flow.svg` and `terminal-session.svg` are drawn by hand.
