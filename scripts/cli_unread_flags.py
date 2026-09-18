#!/usr/bin/env python3
"""CLI flags the parser accepts and no handler reads — advertised no-ops.

An accepted flag that nothing consumes is worse than a missing one: the missing
flag fails loudly and teaches; the accepted one silently discards what the user
supplied. The first run of this probe (2026-09-18) returned 24 dests across
seven verbs, among them a `vision` handler reading three attributes its parser
never defined (every call crashed), six `goals-list` scope filters that were
never applied, and a `monitor` verb whose nine flags fed a deprecation notice.

How it works: build the live argparse tree (`create_argument_parser`), map each
verb to its handler through cli_core's dispatch dict, and for every dest look for
`args.<dest>`, `"<dest>"` or `'<dest>'` in the handler's module. A dest found only
in OTHER handler modules is reported as `elsewhere` (delegation is common); one
found in no handler module at all is `NOWHERE`. The match is deliberately loose,
so a NOWHERE hit is strong and an `elsewhere` hit is a prompt to look.

    python3 scripts/cli_unread_flags.py            # NOWHERE hits only
    python3 scripts/cli_unread_flags.py --all      # include elsewhere-in-handlers
    python3 scripts/cli_unread_flags.py --check    # exit 1 on any NOWHERE hit
"""

from __future__ import annotations

import argparse
import inspect
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

#: dests every handler may legitimately leave to the framework
COMMON = {"output", "verbose", "help", "command", "session_id", "ai_id", "project_id", "quiet", "json"}


def _dispatch_table(src: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in re.finditer(r'"([a-z0-9-]+)":\s*(handle_\w+)', src)}


def find_unread(include_elsewhere: bool = False) -> list[tuple[str, str, str, str]]:
    """(where, verb, dest, handler_module) for every dest with no reader in its handler."""
    from empirica.cli import cli_core
    from empirica.cli import command_handlers as ch
    from empirica.cli.cli_core import create_argument_parser

    parser = create_argument_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    handlers = _dispatch_table(Path(cli_core.__file__).read_text(encoding="utf-8"))
    handler_dir = Path(ch.__file__).parent
    all_handler_src = "".join(f.read_text(encoding="utf-8", errors="replace") for f in handler_dir.rglob("*.py"))

    hits = []
    for verb, p in sorted(sub.choices.items()):
        fn = getattr(ch, handlers.get(verb, ""), None)
        if fn is None:
            continue
        mod_src = inspect.getsource(sys.modules[fn.__module__])
        for a in p._actions:
            d = a.dest
            if d in COMMON or isinstance(a, argparse._SubParsersAction):
                continue
            pats = (f"args.{d}", f'"{d}"', f"'{d}'")
            if any(pt in mod_src for pt in pats):
                continue
            where = "elsewhere" if any(pt in all_handler_src for pt in pats) else "NOWHERE"
            if where == "NOWHERE" or include_elsewhere:
                hits.append((where, verb, d, fn.__module__.rsplit(".", 1)[-1]))
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--all", action="store_true", help="also list dests read only in other handler modules")
    ap.add_argument("--check", action="store_true", help="exit 1 if any dest is read nowhere")
    args = ap.parse_args(argv)
    hits = find_unread(include_elsewhere=args.all)
    for where, verb, d, mod in hits:
        print(f"{where:10} {verb:32} --{d.replace('_', '-'):30} {mod}")
    nowhere = sum(1 for h in hits if h[0] == "NOWHERE")
    print(f"# {len(hits)} listed, {nowhere} read nowhere", file=sys.stderr)
    return 1 if (args.check and nowhere) else 0


if __name__ == "__main__":
    sys.exit(main())
