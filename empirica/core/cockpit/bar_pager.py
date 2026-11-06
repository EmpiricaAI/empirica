"""A small scrolling viewer with a Close button, for the cockpit bar's popups.

David, 2026-11-06: everything that opens a popup needs a close button. `less` scrolls well and has no button: a person reaching the
cockpit through a browser, with only a mouse or a touch screen, could open the help and not get out. This viewer draws the text with
a header that carries three buttons (up, down, Close) and answers the wheel, the arrow and page keys, `q` and Escape as well. A line
in a numbered index is a link when the text has a matching `§N` section: clicking it jumps there.

`Pager` holds the state and the hit-testing and knows nothing about curses, so it is tested without a terminal. `run` is the curses
loop around it.
"""

from __future__ import annotations

import re
from pathlib import Path

CLOSE_LABEL = "[ ✕ Close ]"
UP_LABEL = "[ ▲ ]"
DOWN_LABEL = "[ ▼ ]"
_INDEX_LINE = re.compile(r"^\s+(\d+)  \S")


class Pager:
    """Text, a scroll position and the places a click means something. Row 0 is the header, the last row the footer."""

    def __init__(self, lines: list[str], title: str, height: int, width: int):
        self.lines = lines
        self.title = title
        self.top = 0
        self.resize(height, width)

    def resize(self, height: int, width: int) -> None:
        self.height, self.width = max(height, 3), max(width, 20)
        self.top = min(self.top, self.max_top)

    @property
    def body_rows(self) -> int:
        return self.height - 2

    @property
    def max_top(self) -> int:
        return max(0, len(self.lines) - self.body_rows)

    def scroll(self, delta: int) -> None:
        self.top = max(0, min(self.max_top, self.top + delta))

    def visible(self) -> list[str]:
        return self.lines[self.top : self.top + self.body_rows]

    # ── header and footer ───────────────────────────────────────────────────────────────────────────────────────────────────

    def buttons(self) -> dict[str, tuple[int, int]]:
        """Each button's first and last column on the header row, right-aligned: up, down, close."""
        close_start = self.width - len(CLOSE_LABEL) - 1
        down_start = close_start - len(DOWN_LABEL) - 1
        up_start = down_start - len(UP_LABEL) - 1
        return {
            "up": (up_start, up_start + len(UP_LABEL) - 1),
            "down": (down_start, down_start + len(DOWN_LABEL) - 1),
            "close": (close_start, close_start + len(CLOSE_LABEL) - 1),
        }

    def header(self) -> str:
        spans = self.buttons()
        row = [" "] * self.width
        for i, ch in enumerate(f" {self.title}"[: max(0, spans["up"][0] - 1)]):
            row[i] = ch
        for name, label in (("up", UP_LABEL), ("down", DOWN_LABEL), ("close", CLOSE_LABEL)):
            start = spans[name][0]
            for i, ch in enumerate(label):
                if 0 <= start + i < self.width:
                    row[start + i] = ch
        return "".join(row)

    def footer(self) -> str:
        last = min(len(self.lines), self.top + self.body_rows)
        text = f" lines {self.top + 1}-{last} of {len(self.lines)}  ·  wheel, arrows, PgUp PgDn  ·  q or Close "
        return text[: self.width].ljust(self.width)

    # ── input ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

    def _section_line(self, number: str) -> int | None:
        marker = f"§{number}  "
        for i, line in enumerate(self.lines):
            if line.lstrip().startswith(marker):
                return i
        return None

    def click(self, x: int, y: int) -> str | None:
        """A left click at column ``x``, row ``y``. Returns "close" when the viewer should end."""
        if y == 0:
            for name, (start, end) in self.buttons().items():
                if start <= x <= end:
                    if name == "close":
                        return "close"
                    self.scroll(-self.body_rows if name == "up" else self.body_rows)
            return None
        if 1 <= y <= self.body_rows:
            index = self.top + y - 1
            match = _INDEX_LINE.match(self.lines[index]) if index < len(self.lines) else None
            target = self._section_line(match.group(1)) if match else None
            if target is not None and target != index:
                self.top = min(self.max_top, target)
        return None

    def key(self, name: str) -> str | None:
        """A key by name: up, down, pgup, pgdn, home, end, q, esc. Returns "close" when the viewer should end."""
        if name in ("q", "esc"):
            return "close"
        steps = {"up": -1, "down": 1, "pgup": -self.body_rows, "pgdn": self.body_rows, "wheelup": -3, "wheeldown": 3}
        if name in steps:
            self.scroll(steps[name])
        elif name == "home":
            self.top = 0
        elif name == "end":
            self.top = self.max_top
        return None


def _draw(screen, pager: Pager, curses) -> None:
    screen.erase()
    rows = [(0, pager.header(), curses.A_REVERSE)]
    rows += [(y + 1, line, curses.A_NORMAL) for y, line in enumerate(pager.visible())]
    rows.append((pager.height - 1, pager.footer(), curses.A_DIM))
    for y, line, attr in rows:
        try:
            screen.addstr(y, 0, line[: pager.width - 1], attr)
        except curses.error:
            continue  # the bottom-right cell cannot be written; the rest of the row is drawn
    screen.refresh()


def _mouse(pager: Pager, curses) -> str | None:
    """One mouse event: the wheel scrolls, a left press is a click. Returns "close" when the viewer should end."""
    try:
        _id, x, y, _z, state = curses.getmouse()
    except curses.error:
        return None
    if state & curses.BUTTON4_PRESSED:
        return pager.key("wheelup")
    if state & getattr(curses, "BUTTON5_PRESSED", 0x200000):
        return pager.key("wheeldown")
    if state & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
        return pager.click(x, y)
    return None


def run(path: Path, title: str) -> int:
    """Show the file at ``path`` until it is closed. Falls back to printing it when curses cannot start."""
    text = path.read_text(errors="replace")
    try:
        import curses
        import locale
    except Exception:
        print(text)
        return 0

    def loop(screen) -> None:
        curses.curs_set(0)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(0)  # report a press at once: a click must not wait for a double-click window
        height, width = screen.getmaxyx()
        pager = Pager(text.splitlines(), title, height, width)
        keys = {
            curses.KEY_UP: "up",
            curses.KEY_DOWN: "down",
            curses.KEY_PPAGE: "pgup",
            curses.KEY_NPAGE: "pgdn",
            curses.KEY_HOME: "home",
            curses.KEY_END: "end",
            ord("k"): "up",
            ord("j"): "down",
            ord(" "): "pgdn",
            ord("g"): "home",
            ord("G"): "end",
            ord("q"): "q",
            27: "esc",
        }
        while True:
            _draw(screen, pager, curses)
            ch = screen.getch()
            if ch == curses.KEY_RESIZE:
                pager.resize(*screen.getmaxyx())
            elif ch == curses.KEY_MOUSE:
                if _mouse(pager, curses) == "close":
                    return
            elif ch in keys and pager.key(keys[ch]) == "close":
                return

    try:
        locale.setlocale(locale.LC_ALL, "")
        curses.wrapper(loop)
    except Exception:
        print(text)
        try:
            input("press Enter to close ")
        except EOFError:
            pass
    return 0
