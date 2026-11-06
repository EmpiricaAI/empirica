"""The text of the cockpit's help popup: the legend, the commands, then the guide to working with practices.

The guide is mesh-support's draft for non-developers (empirica-mesh-support docs/guide at 562cedab), corrected by core to the
cockpit as built, and shipped with the package under core/cockpit/guide/. Its content is owned by core with David (2026-11-06):
edit the pages there. Markers of the form [check: ...] are facts not yet confirmed; they stay visible until someone settles them.

The pages are markdown and are rendered here to plain text at the popup's width: headings become numbered sections the viewer
can jump to (a line "  §N  TITLE"), the index lines link to them, tables become one wrapped line per row.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

GUIDE_DIR = Path(__file__).parent / "guide"
WIDTH = 96

LEGEND = """\
  EMPIRICA COCKPIT  ·  wheel, arrows or PgUp/PgDn scroll  ·  click a topic number to jump to it
  ────────────────────────────────────────────────────────────────────────────────────

  THE BARS
    Top          your practices (click one, or Ctrl+b then its number), health on the right.
                 A red ✗ on a practice means a pane in it has stopped.
    Under each   the commands, coloured by state. Click anywhere on that line to open
    pane         the command menu. The colour says whether you need to act:

      dim      nothing to do
      orange   recommended: something is waiting (mail, an update, a login about to expire)
      red      needed now: something is broken (a stopped pane, a listener that is down)

    When there are too many practices to fit, health moves to a second line at the top.

  COMMANDS   click the line under a pane and choose. From a keyboard: Ctrl+b, then e,
             then the letter, or Ctrl+b, e, Space for the menu
    R  restart this pane    same practice, same identity, conversation resumed, then woken
    A  refresh all          restart every pane whose program stopped; running ones are left alone
    M  listener             wake this practice so it reads its mail and re-arms its listener
    I  inbox                show this practice's unanswered mail
    C  claude               update the AI client (asks first)
    S  sessions             restart every practice pane onto new settings (asks first)
    L  login                renew the Cortex login, in a window of its own
    ?  this help

    The menu also has "update Empirica on this machine", near its end. It has no chip and no
    key, so it is not started by accident; it asks before it does anything.

    R, A and M never touch a pane that is running. Update, C and S always ask first, with Yes and
    Cancel you can click. Every menu has a Close item; this help has a Close button at the top.

"""


def _plain(text: str) -> str:
    """Markdown emphasis, code marks and links reduced to their text."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("**", "").replace("`", "")
    return re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"\1", text)


def _wrap(text: str, indent: int, hanging: int | None = None) -> list[str]:
    first = " " * indent
    rest = " " * (indent if hanging is None else hanging)
    return textwrap.wrap(text, width=WIDTH, initial_indent=first, subsequent_indent=rest) or [""]


def _render_page(markdown: str) -> list[str]:
    """One guide page as plain lines no wider than WIDTH."""
    out: list[str] = []
    paragraph: list[str] = []
    in_code = False

    def flush() -> None:
        if paragraph:
            out.extend(_wrap(_plain(" ".join(paragraph)), 4))
            paragraph.clear()

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            flush()
            in_code = not in_code
            continue
        if in_code:
            out.append(("      " + line)[:WIDTH])
            continue
        heading = re.match(r"^# (\d+)\. (.+)$", line)
        if heading:
            flush()
            out += ["", "", f"  §{heading.group(1)}  {_plain(heading.group(2)).upper()}", "  " + "─" * (WIDTH - 4)]
            continue
        sub = re.match(r"^#{2,} (.+)$", line)
        if sub:
            flush()
            out += ["", f"  {_plain(sub.group(1))}"]
            continue
        if line.startswith("|"):
            flush()
            cells = [_plain(c.strip()) for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
                continue
            out.extend(_wrap(" · ".join(c for c in cells if c), 4, 8))
            continue
        item = re.match(r"^(\s*)([-*]|\d+\.) (.+)$", line)
        if item:
            flush()
            marker = "-" if item.group(2) in "-*" else item.group(2)
            depth = 4 + len(item.group(1))
            out.extend(_wrap(f"{marker} {_plain(item.group(3))}", depth, depth + len(marker) + 1))
            continue
        if not line.strip():
            flush()
            if out and out[-1] != "":
                out.append("")
            continue
        paragraph.append(line.strip())
    flush()
    return out


def _index(readme: str) -> list[str]:
    """The guide's table of pages as viewer index lines: number, two spaces, title, and when to read it."""
    rows = re.findall(r"^\| (\d+) \| \[([^\]]+)\]\([^)]+\) \| ([^|]+?) \|$", readme, flags=re.M)
    lines = ["  THE GUIDE   click a page number to jump to it; g or Home returns here", ""]
    for number, title, when in rows:
        lines.extend(_wrap(f"{number:>2}  {_plain(title)}  ·  {_plain(when)}", 4, 10))
    return lines


def help_text(guide_dir: Path = GUIDE_DIR) -> str:
    """The legend and commands, then the guide's index, its three words and every page."""
    readme = (guide_dir / "README.md").read_text()
    words = readme[readme.index("## Three words") :] if "## Three words" in readme else ""
    lines = LEGEND.rstrip("\n").splitlines() + [""] + _index(readme)
    if words:
        lines += _render_page(words)
    for page in sorted(guide_dir.glob("[0-9][0-9]-*.md")):
        lines += _render_page(page.read_text())
    return "\n".join(lines).rstrip() + "\n"


HELP_TEXT = help_text()
