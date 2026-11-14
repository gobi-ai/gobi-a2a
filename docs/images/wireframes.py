"""Wireframes of the Gobi app's Workers screens, for gobi-a2a's docs/images.

Follows lib/scenes/main/explore/agent_workers_page.dart and agent_worker_widgets.dart: the same labels, order and
pieces (stat tiles, worker avatars, tone pills, callouts), drawn in the docs' existing wireframe style.
    python3 docs/images/wireframes.py docs/images
"""
from __future__ import annotations

import re
import sys
from html import escape
from pathlib import Path

W, H = 320, 680
X0, CW = 18, 284                                   # content left and width
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
INK, INK2, INK3, LINE, CARD, ORANGE = "#1f2328", "#59636e", "#8c959f", "#d0d7de", "#f6f8fa", "#e8590c"
TONES = {                                          # GobiBadgeTone: (tint, ink)
    "neutral": ("#eff2f5", "#59636e"), "info": ("#ddf4ff", "#0969da"), "success": ("#dafbe1", "#1a7f37"),
    "warning": ("#fff1d6", "#9a6700"), "error": ("#ffebe9", "#cf222e"),
}
HUES = {"orange": ("#ffeedd", "#bc4c00"), "teal": ("#d6f5f0", "#0f766e"), "green": ("#dafbe1", "#1a7f37"),
        "blue": ("#ddf4ff", "#0969da"), "pink": ("#ffe5f1", "#bf3989"), "purple": ("#f1e8ff", "#8250df")}
LOOKS = {"claude": ("✦", "orange"), "researcher": ("◍", "teal"), "calendar": ("▦", "green")}   # workerLook


def look(name: str) -> tuple[str, str]:
    return next((v for k, v in LOOKS.items() if k in name.lower()), ("◍", "blue"))


class Screen:
    def __init__(self, title: str, gear: bool = False):
        self.out: list[str] = []
        self.rect(1, 1, W - 2, H - 42, 36, "#ffffff", "#afb8c1", 2)
        self.rect(120, 14, 80, 22, 11, INK)
        self.out.append(f'<line x1="0" y1="96" x2="{W}" y2="96" stroke="{LINE}"/>')
        self.text(26, 76, "‹", 26, INK2, anchor="middle")
        self.text(160, 76, title, 16, INK, 600, "middle")
        if gear:
            self.text(292, 77, "⚙", 18, INK2, anchor="middle")

    def rect(self, x, y, w, h, rx, fill, stroke="none", sw=1):
        self.out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" '
                        f'stroke-width="{sw}"/>')

    def text(self, x, y, s, size=12, fill=INK2, weight=400, anchor="start", italic=False):
        style = ' font-style="italic"' if italic else ""
        self.out.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" font-weight="{weight}" '
                        f'text-anchor="{anchor}"{style}>{escape(s)}</text>')

    def lines(self, x, y, rows, size=12, fill=INK2, weight=400, step=None) -> float:
        step = step or round(size * 1.4)
        for i, r in enumerate(rows):
            self.text(x, y + i * step, r, size, fill, weight)
        return y + (len(rows) - 1) * step

    def label(self, y, s, badge: str | None = None):
        """SettingsSectionLabel: the label in tertiary ink, and a count badge."""
        self.text(X0 + 2, y, s, 12, INK3, 600)
        if badge:
            bx = X0 + 2 + len(s) * 6.8 + 8
            self.rect(bx, y - 12, 20, 16, 8, TONES["neutral"][0])
            self.text(bx + 10, y, badge, 10, INK2, 600, "middle")

    def card(self, x, y, w, h):
        self.rect(x, y, w, h, 14, CARD)

    def avatar(self, x, y, name, size=40):
        glyph, hue = look(name)
        tint, ink = HUES[hue]
        self.rect(x, y, size, size, 10 if size < 56 else 16, tint)
        self.text(x + size / 2, y + size / 2 + 6, glyph, 18 if size < 56 else 26, ink, 600, "middle")

    def pill(self, x, y, glyph, s, tone="neutral") -> float:
        """WorkerTonePill; returns where the next one starts."""
        tint, ink = TONES[tone]
        w = 26 + len(s) * 5.9
        self.rect(x, y, w, 22, 11, tint)
        self.text(x + 10, y + 15, glyph, 10, ink, 700)
        self.text(x + 22, y + 15, s, 11, ink, 600)
        return x + w + 6

    def pills(self, x, y, items):
        for glyph, s, tone in items:
            x = self.pill(x, y, glyph, s, tone)

    def callout(self, y, rows, tone="warning", glyph="⚠") -> float:
        """WorkerCallout: a tinted sentence with a glyph; returns its bottom."""
        h = 16 + len(rows) * 15
        self.rect(X0, y, CW, h, 12, TONES[tone][0])
        self.text(X0 + 12, y + 20, glyph, 12, TONES[tone][1], 700)
        self.lines(X0 + 30, y + 20, rows, 11, INK, step=15)
        return y + h

    def stat(self, x, y, w, glyph, value, label, tone="neutral"):
        """WorkerStatTile."""
        self.rect(x, y, w, 92, 16, CARD)
        tint, ink = TONES[tone]
        self.rect(x + 12, y + 12, 28, 28, 8, tint)
        self.text(x + 26, y + 31, glyph, 13, ink, 700, "middle")
        self.text(x + 12, y + 64, value, 20, INK, 600)
        self.text(x + 12, y + 81, label, 11, INK3)

    def stats(self, y, tiles):
        w = (CW - 8 * (len(tiles) - 1)) / len(tiles)
        for i, t in enumerate(tiles):
            self.stat(X0 + i * (w + 8), y, w, *t)

    def field(self, y, value, secret=False):
        self.rect(X0, y, CW, 38, 10, "#ffffff", LINE)
        self.text(X0 + 12, y + 24, "•" * 24 if secret else value, 13, INK)

    def button(self, x, y, w, s, kind="secondary", lead=""):
        fill, ink, stroke = {"fill": (INK, "#fff", INK), "secondary": ("#ffffff", INK, LINE)}[kind]
        self.rect(x, y, w, 38, 19, fill, stroke)
        self.text(x + w / 2, y + 24, f"{lead}  {s}" if lead else s, 13, ink, 600, "middle")

    def empty(self, y, title, rows, glyph="🤖") -> float:
        """EmptyState on a WorkerCard; returns its bottom."""
        h = 92 + len(rows) * 15
        self.card(X0, y, CW, h)
        self.out.append(f'<circle cx="160" cy="{y + 34}" r="18" fill="{TONES["neutral"][0]}"/>')
        self.text(160, y + 40, glyph, 16, INK2, anchor="middle")
        self.text(160, y + 72, title, 13, INK, 600, "middle")
        for i, r in enumerate(rows):
            self.text(160, y + 92 + i * 15, r, 11, INK2, anchor="middle")
        return y + h

    def worker_card(self, y, name, desc_rows, pill_rows) -> float:
        """_WorkerSummaryCard: face, name, what it is for, chevron, then its pills (a row each)."""
        top = y + max(58, 52 + 14 * len(desc_rows)) + 6
        h = top - y + 28 * len(pill_rows) + 8
        self.card(X0, y, CW, h)
        self.avatar(X0 + 14, y + 14, name)
        self.text(X0 + 66, y + 30, name, 14, INK, 600)
        self.lines(X0 + 66, y + 47, desc_rows, 11, INK2, step=14)
        self.text(X0 + CW - 14, y + 31, "›", 16, INK3, anchor="end")
        for i, row in enumerate(pill_rows):
            self.pills(X0 + 14, top + 28 * i, row)
        return y + h

    def mark(self, x, y, w, h, n, rx=11):
        """An orange outline and its step number."""
        self.rect(x, y, w, h, rx, "none", ORANGE, 2.5)
        self.out.append(f'<circle cx="{x + w}" cy="{y}" r="11" fill="{ORANGE}"/>')
        self.text(x + w, y + 4.5, str(n), 12, "#fff", 700, "middle")

    def toast(self, y, s):
        w = 40 + len(s) * 6.6
        self.rect(160 - w / 2, y, w, 34, 17, INK)
        self.text(160, y + 22, s, 12, "#fff", 600, "middle")

    def svg(self, footer=True) -> str:
        body = "".join(self.out)
        if footer:
            body += (f'<text x="160.0" y="666" font-size="12" fill="{INK3}" font-weight="400" text-anchor="middle" '
                     f'font-style="italic">Wireframe — replace with a screenshot</text>')
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
                f'font-family="{FONT}"><title>Wireframe — replace with a screenshot</title>{body}</svg>')


INTRO_WORKERS = ["Your agent hands longer tasks to a worker. The", "ones at work now are here; the gear sets up",
                 "which workers it has."]
INTRO_SETTINGS = ["The workers your agent can hand a task to.", "Gobi's own are fixed; you can add your own, or",
                  "a remote agent such as Claude Code running on", "your computer."]
CLAUDE_DESC = ["Claude Code working in its own folder:", "code, files and web research."]
REMOTE_WARNING = ["A remote agent can do whatever it can, so your", "agent starts it only when you ask in your own",
                  "message."]


def workers_empty() -> str:
    s = Screen("Workers", gear=True)
    s.mark(274, 58, 36, 36, 3, rx=17)
    s.lines(X0 + 2, 124, INTRO_WORKERS)
    s.stats(170, [("ϟ", "0", "Running", "info"), ("⧗", "0", "Waiting"), ("✓", "0", "Done today", "success")])
    s.label(292, "Running")
    s.empty(304, "Nothing running", ["When your agent hands a worker a task, you", "can follow it here."])
    return s.svg()


def settings_bottom(s: Screen, y: float, mark: bool) -> None:
    """The New worker / Remote agent buttons, then Gobi's own."""
    half = (CW - 8) / 2
    s.button(X0, y, half, "New worker", lead="+")
    s.button(X0 + half + 8, y, half, "Remote agent", lead="◍")
    if mark:
        s.mark(X0 + half + 4, y - 4, half + 8, 46, 4, rx=22)
    s.label(y + 72, "From Gobi", badge="5")
    gobi = [[("◉", "Read only", "success"), ("○", "No runs this week", "neutral")]]
    bottom = s.worker_card(y + 84, "researcher", ["Looks things up on the web and reads", "pages."], gobi)
    if bottom + 100 < H - 44:
        s.worker_card(bottom + 8, "calendar", ["Reads the user's Google calendars."], gobi)


def settings_empty() -> str:
    s = Screen("Worker settings")
    s.lines(X0 + 2, 124, INTRO_SETTINGS)
    s.label(202, "Yours")
    bottom = s.empty(214, "No workers of your own yet", ["Make one with its own tools, or add a", "remote agent by its address."])
    settings_bottom(s, bottom + 12, mark=True)
    return s.svg()


def settings_added() -> str:
    s = Screen("Worker settings")
    s.lines(X0 + 2, 124, INTRO_SETTINGS)
    s.label(202, "Yours", badge="1")
    bottom = s.worker_card(214, "claude-code", CLAUDE_DESC,
                           [[("◍", "Remote agent", "neutral"), ("✎", "Changes things", "warning")],
                            [("○", "No runs this week", "neutral")]])
    settings_bottom(s, bottom + 12, mark=False)
    s.toast(592, "Added claude-code")
    return s.svg()


def address() -> str:
    s = Screen("Remote agent")
    s.label(122, "Address")
    s.field(130, "https://my-mac.tail1234.ts.net/claude")
    s.mark(X0 - 4, 126, CW + 8, 46, 5, rx=12)
    s.lines(X0 + 2, 190, ["Its https address, e.g. the tunnel to Claude Code on", "your computer."], 11, INK3, step=15)
    s.button(X0, 224, CW, "Look it up")
    s.mark(X0 - 4, 220, CW + 8, 46, 6, rx=22)
    return s.svg()


def card_and_sign_in(s: Screen, skills: bool) -> tuple[float, float]:
    """Address, What it says it is, the warning, Name and Token; returns (token field y, its bottom)."""
    s.label(122, "Address")
    s.field(130, "https://my-mac.tail1234.ts.net/claude")
    s.label(196, "What it says it is")
    h = 132 if skills else 84
    s.card(X0, 206, CW, h)
    s.avatar(X0 + 14, 220, "claude")
    s.text(X0 + 66, 236, "Claude Code", 14, INK, 600)
    s.lines(X0 + 66, 253, CLAUDE_DESC, 11, INK2, step=14)
    if skills:
        s.text(X0 + 14, 300, "Skills · 1", 11, INK3, 600)
        s.rect(X0 + 14, 310, 160, 22, 11, "#ffffff", LINE)
        s.text(X0 + 24, 325, "✦  Work on files and the web", 11, INK2, 600)
    y = s.callout(206 + h + 10, REMOTE_WARNING)
    s.label(y + 26, "Name")
    s.field(y + 34, "claude-code")
    s.label(y + 96, "Token")
    s.field(y + 104, "", secret=True)
    s.text(X0 + 2, y + 160, "Sent only to this agent, and never shown again.", 11, INK3)
    return y + 104, y + 160


def token() -> str:
    s = Screen("Remote agent")
    token_y, bottom = card_and_sign_in(s, skills=True)
    s.mark(X0 - 4, token_y - 4, CW + 8, 46, 7, rx=12)
    s.button(X0, bottom + 18, CW, "Add", kind="fill")
    s.mark(X0 - 4, bottom + 14, CW + 8, 46, 8, rx=22)
    return s.svg()


def running() -> str:
    s = Screen("Workers", gear=True)
    s.lines(X0 + 2, 124, INTRO_WORKERS)
    s.stats(170, [("ϟ", "1", "Running", "info"), ("⧗", "0", "Waiting"), ("✓", "1", "Done today", "success")])
    s.label(292, "Running", badge="1")
    s.card(X0, 304, CW, 150)                       # _RunningTicketCard
    s.avatar(X0 + 14, 318, "claude-code")
    s.text(X0 + 66, 334, "Fix the flaky upload test", 14, INK, 600)
    s.text(X0 + 66, 351, "claude-code · started 2 min ago", 11, INK3)
    s.text(X0 + CW - 14, 336, "⊗", 18, INK3, anchor="end")
    s.pill(X0 + 14, 372, "ϟ", "Running", "info")
    s.rect(X0 + 14, 404, CW - 28, 36, 10, "#eaeef2")
    s.text(X0 + 26, 427, "↳", 12, INK3, 600)
    s.text(X0 + 42, 427, "Running the test suite…", 11, INK2)
    s.label(484, "Recently finished")
    s.card(X0, 496, CW, 104)                       # _FinishedTicketRow
    s.avatar(X0 + 14, 512, "claude-code")
    s.text(X0 + 66, 528, "Summarize the A2A spec", 13, INK, 600)
    s.text(X0 + 66, 545, "Wrote docs/summary.md", 11, INK2)
    x = s.pill(X0 + 66, 560, "✓", "Done", "success")
    s.text(x + 2, 575, "claude-code · 1 h ago", 11, INK3)
    return s.svg()


def phone_for_terminal() -> tuple[str, float, float]:
    """The Remote agent screen inside terminal-to-app.svg: its elements, and where Address and Token are."""
    s = Screen("Remote agent")
    token_y, bottom = card_and_sign_in(s, skills=False)
    s.button(X0, bottom + 18, CW, "Add", kind="fill")
    for y, n in ((130, "A"), (token_y, "B")):
        s.rect(X0 - 4, y - 4, CW + 8, 46, 10, "none", ORANGE, 2.5)
        s.out.append(f'<circle cx="{X0 + CW + 4}" cy="{y - 4}" r="12" fill="{ORANGE}"/>')
        s.text(X0 + CW + 4, y + 0.5, n, 12, "#fff", 700, "middle")
    return "".join(s.out), 130, token_y


def terminal_to_app(path: Path) -> None:
    """Swap the phone in the composite for the new Remote agent screen and point the Token arrow at its field."""
    svg = path.read_text()
    start = svg.index('<g transform="translate(760 22) scale(0.9)">') + len('<g transform="translate(760 22) scale(0.9)">')
    end = svg.index("</g>", start)
    phone, _, token_y = phone_for_terminal()
    svg = svg[:start] + phone + svg[end:]
    to = 22 + 0.9 * (token_y + 19)                 # the field's middle, in the composite's coordinates
    svg = re.sub(r'<path d="M433 262 C605 262 605 [\d.]+ 772 [\d.]+"',
                 f'<path d="M433 262 C605 262 605 {to:.0f} 772 {to:.0f}"', svg)
    svg = re.sub(r'(<text x="762.2" y=")[\d.]+(" [^>]*>paste in Token)', rf'\g<1>{to - 12.2:.1f}\2', svg)
    path.write_text(svg)


if __name__ == "__main__":
    out = Path(sys.argv[1])
    for name, make in {"02-workers.svg": workers_empty, "03-worker-settings.svg": settings_empty,
                       "04-remote-agent-address.svg": address, "05-remote-agent-token.svg": token,
                       "06-worker-added.svg": settings_added, "07-workers-running.svg": running}.items():
        (out / name).write_text(make())
    terminal_to_app(out / "terminal-to-app.svg")
    print("written")
