# docs/images

The pictures in [Connecting to Gobi App](../connecting-gobi-app.md) (two of them also appear in the main
[README](../../README.md)).

The terminal images show `gobi-a2a`'s real output (only the token and paths are examples). The phone screens are
**wireframes**, not screenshots. They follow the Gobi app's Workers pages as of
[gobi-app#1147](https://github.com/gobi-ai/gobi-app/pull/1147) (`lib/scenes/main/explore/agent_workers_page.dart`),
with the same labels and order. Orange numbered outlines mark what to tap, and the numbers match the walkthrough's steps.

| File | Screen | Walkthrough step |
|---|---|---|
| `connection-flow.svg` | Diagram: Gobi app → Gobi harness → https tunnel → gobi-a2a → Claude Code | overview |
| `terminal-session.svg` | Terminal: `tailscale funnel --bg 8000`, then `gobi-a2a` and its *Agent URLs ready* block | on your computer |
| `terminal-to-app.svg` | The address (A) and token (B) copied from the terminal into **Remote agent** | terminal to app |
| `01-personal-agent-menu.svg` | Personal agent chat, the title pill's menu open | 1–2: title pill ▸ **Workers** |
| `02-workers.svg` | **Workers**, nothing running | 3: gear ▸ **Worker settings** |
| `03-worker-settings.svg` | **Worker settings**, nothing under Yours | 4: **Remote agent** |
| `04-remote-agent-address.svg` | **Remote agent**, Address filled in | 5–6: Address ▸ **Look it up** |
| `05-remote-agent-token.svg` | **Remote agent** after the look-up: the card, Name, Token | 7–8: Token ▸ **Add** |
| `06-worker-added.svg` | **Worker settings** with `claude-code` under Yours and the *Added* toast | result |
| `07-workers-running.svg` | **Workers** with a running task and a recently finished one | using it |

## Replacing a wireframe with a screenshot

1. Take the screenshot on a phone (light mode reads best on GitHub). Cover anything private: the token field is
   masked, but your agent's name, tasks, and a tailnet address can show.
2. Crop it to the screen, and keep it under about 300 KB (`pngquant` or `sips -Z 1200` helps).
3. Save it here with the **same name** and a `.png` extension, for example `03-worker-settings.png`.
4. In `../connecting-gobi-app.md` (and `../../README.md`, for the two it shows), change that
   `<img src="images/03-worker-settings.svg" ...>` to `.png`, and delete the `.svg`.

Keep the `width="240"` on phone screens so the three-across rows still fit. If the app's labels change, update the
walkthrough's steps and this table at the same time.
