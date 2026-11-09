"""Dotted-path checks for the contract's ``response.required`` entries.

``a.b[].c`` means: ``a`` is an object, ``b`` is a list, and every element of it has ``c``. An empty list satisfies the
path (there is no element to be wrong), so a mailbox with no mail is not a failure. An entry written as
``id or title (note)`` is satisfied by any one of the alternatives.
"""
from __future__ import annotations

import re


def alternatives(entry: str) -> list[str]:
    head = re.sub(r"\s*\(.*$", "", entry).strip()
    return [p.strip() for p in head.split(" or ")]


def missing(obj, path: str) -> list[str]:
    """The places where ``path`` is not satisfied by ``obj`` (empty when it is)."""
    parts = path.split(".")
    problems: list[str] = []

    def walk(node, rest, trail):
        if not rest:
            return
        seg, tail = rest[0], rest[1:]
        is_list = seg.endswith("[]")
        key = seg[:-2] if is_list else seg
        if not isinstance(node, dict) or key not in node:
            problems.append(f"{trail}{key}")
            return
        value = node[key]
        if is_list:
            if not isinstance(value, list):
                problems.append(f"{trail}{key} (expected a list)")
                return
            for i, item in enumerate(value):
                walk(item, tail, f"{trail}{key}[{i}].")
        else:
            walk(value, tail, f"{trail}{key}.")

    walk(obj, parts, "")
    return problems


def unsatisfied(obj, entry: str) -> list[str]:
    alts = alternatives(entry)
    results = [missing(obj, a) for a in alts]
    return [] if any(not r for r in results) else results[0]
