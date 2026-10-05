#!/usr/bin/env python3
"""Condense an agent transcript (jsonl) into a numbered trace a reviewer can read and cite.

    python3 build_trace.py TRANSCRIPT.jsonl --out-dir DIR [--name NAME]

Writes DIR/trace_NAME.txt (one block per turn: "[T<n>] <role>: <text>") and DIR/turns_NAME.json (the
same turns as data, for check_stamp.py). Roles: assistant (the agent's own words), tool_call, tool_result.
Tool calls and results are truncated; a reviewer can only quote what the trace shows, so raise the limits
if the evidence you need is being cut.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TOOL_CALL_LIMIT = 500
TOOL_RESULT_LIMIT = 700


def _blocks(entry: dict) -> list[tuple[str, dict]]:
    msg = entry.get("message") or {}
    content = msg.get("content")
    role = msg.get("role")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    return [(role or "", b) for b in content if isinstance(b, dict)]


def _result_text(block: dict) -> str:
    body = block.get("content")
    if isinstance(body, list):
        body = " ".join(part.get("text", "") for part in body if isinstance(part, dict))
    return body or ""


def build_turns(path: str | Path, call_limit: int = TOOL_CALL_LIMIT, result_limit: int = TOOL_RESULT_LIMIT) -> list[dict]:
    """Return the numbered turns of a transcript: [{"n": 1, "role": ..., "text": ...}, ...]."""
    turns: list[tuple[str, str]] = []
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        for role, block in _blocks(entry):
            kind = block.get("type")
            if kind == "text" and role == "assistant":
                turns.append(("assistant", block.get("text", "")))
            elif kind == "tool_use":
                args = block.get("input") or {}
                shown = args.get("command") or args.get("file_path") or args.get("pattern") or json.dumps(args)[:200]
                turns.append(("tool_call", f"{block.get('name')}: {shown}"[:call_limit]))
            elif kind == "tool_result":
                turns.append(("tool_result", _result_text(block)[:result_limit]))
    return [{"n": i, "role": r, "text": t} for i, (r, t) in enumerate(turns, 1)]


def render_trace(turns: list[dict]) -> str:
    return "\n\n".join(f"[T{t['n']}] {t['role']}: {t['text']}" for t in turns)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("transcript")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--name", default=None, help="label for the output files (default: the transcript's stem)")
    ap.add_argument("--call-limit", type=int, default=TOOL_CALL_LIMIT)
    ap.add_argument("--result-limit", type=int, default=TOOL_RESULT_LIMIT)
    args = ap.parse_args(argv)

    turns = build_turns(args.transcript, args.call_limit, args.result_limit)
    if not turns:
        print(f"no turns found in {args.transcript}: wrong file, or an unsupported transcript shape", file=sys.stderr)
        return 1
    name = args.name or Path(args.transcript).stem
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"trace_{name}.txt").write_text(render_trace(turns))
    (out / f"turns_{name}.json").write_text(json.dumps(turns))
    print(json.dumps({"name": name, "turns": len(turns), "trace": str(out / f"trace_{name}.txt")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
