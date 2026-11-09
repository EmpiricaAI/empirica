"""The Sentinel's discipline mode: small, project-local work outside a transaction, up to a budget the user sets (goal 543fd1c6).

mesh-support's design, accepted by David 2026-11-05: the Sentinel is a discipline and measurement layer, not a security boundary.
In ``discipline`` mode a write or an effectful command outside a transaction is allowed while it stays trivial: it changes only
the practice's own files (or its own Empirica state, or makes a local commit), its effect is readable from its text, and fewer
than ``max_act_without_gate`` such acts have happened since the last transaction. Each one is written to an out-of-loop ledger
that the next PREFLIGHT reads. Anything outward or irreversible (a push, a tag, a publish, an install, a recursive delete, a
remote command, a network send, a change to configuration or outside the practice) always needs a transaction.

``strict`` (the built-in default until the pilot decides the flip) keeps the gate as it was: this module has no opinion.
``off`` allows every call and records it, like a paused Sentinel.

Settings, highest first: the environment (``EMPIRICA_SENTINEL_MODE``, ``EMPIRICA_MAX_ACT_WITHOUT_GATE``), the practice's
``.empirica/config.yaml``, the seat's ``~/.empirica/config.yaml`` (both under a top-level ``sentinel:`` block with ``mode`` and
``max_act_without_gate``), then the built-in values. Every resolved value carries where it came from.

The gate loads this file by path (never through the package, which costs an import on every call) and consults it only on
its out-of-transaction branch, so calls inside a transaction pay nothing. Stdlib only, except PyYAML, imported only when a
setting has to be read from a file.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import time
from pathlib import Path

MODES = ("strict", "discipline", "off")
#: Until the pilot (3-4 practices, two weeks) decides the flip for 1.15.0.
DEFAULT_MODE = "strict"
#: Predicted, pending David's ruling: an edit, a local commit and one more act (the lean prompt's "a one-line fix, a typo, a
#: config value" sized as acts). Autonomous seats are meant to run at 0; that is set per seat, not detected.
DEFAULT_BUDGET = 3
ENV_MODE = "EMPIRICA_SENTINEL_MODE"
ENV_BUDGET = "EMPIRICA_MAX_ACT_WITHOUT_GATE"
BUDGET_MAX = 1000

#: What discipline mode runs outside a transaction is an ALLOWLIST, and it fails closed. An earlier draft allowed anything whose
#: effects the decision record inferred as project-local; the 1.14.23 sweep found that inference let git config / clean / reset /
#: clone, sed -ri, curl -o, cp -t, symlinks and git -C through (finding fddc9b63). The inference stays a record, not a gate.
_GIT_VERBS = {"add", "commit", "mv", "rm"}
_COMMANDS = {"git", "mkdir", "touch"}
#: Directories inside a practice whose files configure tools that run code or govern this mode: never written out of loop.
_PROTECTED_PARTS = {".git", ".claude", ".codex", ".empirica", ".vscode", ".idea"}
_PUNCTUATION = set("();<>|&")
_FILE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


# ── settings ────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _sentinel_block(path: Path) -> dict:
    """The ``sentinel:`` mapping of a config file; {} when the file or the block is absent or unreadable."""
    if not path.is_file():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    block = data.get("sentinel") if isinstance(data, dict) else None
    return block if isinstance(block, dict) else {}


def _valid_mode(value) -> str | None:
    if value is False:
        return "off"  # YAML 1.1 reads a bare `off` as false; `write_setting` quotes it, a hand-written file may not
    if isinstance(value, bool):
        return None
    text = str(value).strip().lower() if value is not None else ""
    return text if text in MODES else None


def _valid_budget(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= BUDGET_MAX else None


def config_paths(project_root: str | Path | None) -> list[tuple[str, Path]]:
    """Where settings are read from, highest precedence first (after the environment)."""
    out = []
    if project_root:
        out.append(("practice", Path(project_root) / ".empirica" / "config.yaml"))
    out.append(("seat", Path.home() / ".empirica" / "config.yaml"))
    return out


def effective(project_root: str | Path | None) -> dict:
    """The mode and budget in force here, each with its source (env, practice, seat or built-in), and any value ignored."""
    result: dict = {"mode": None, "mode_source": None, "budget": None, "budget_source": None, "ignored": []}
    env_mode, env_budget = os.environ.get(ENV_MODE), os.environ.get(ENV_BUDGET)
    if env_mode is not None:
        if _valid_mode(env_mode):
            result["mode"], result["mode_source"] = _valid_mode(env_mode), f"env {ENV_MODE}"
        else:
            result["ignored"].append(f"{ENV_MODE}={env_mode!r} is not one of {', '.join(MODES)}")
    if env_budget is not None:
        if _valid_budget(env_budget) is not None:
            result["budget"], result["budget_source"] = _valid_budget(env_budget), f"env {ENV_BUDGET}"
        else:
            result["ignored"].append(f"{ENV_BUDGET}={env_budget!r} is not a whole number from 0 to {BUDGET_MAX}")
    if result["mode"] is None or result["budget"] is None:
        for name, path in config_paths(project_root):
            block = _sentinel_block(path)
            if result["mode"] is None and "mode" in block:
                if _valid_mode(block["mode"]):
                    result["mode"], result["mode_source"] = _valid_mode(block["mode"]), f"{name} {path}"
                else:
                    result["ignored"].append(f"sentinel.mode {block['mode']!r} in {path}")
            if result["budget"] is None and "max_act_without_gate" in block:
                if _valid_budget(block["max_act_without_gate"]) is not None:
                    result["budget"] = _valid_budget(block["max_act_without_gate"])
                    result["budget_source"] = f"{name} {path}"
                else:
                    result["ignored"].append(
                        f"sentinel.max_act_without_gate {block['max_act_without_gate']!r} in {path}"
                    )
    if result["mode"] is None:
        result["mode"], result["mode_source"] = DEFAULT_MODE, "built-in"
    if result["budget"] is None:
        result["budget"], result["budget_source"] = DEFAULT_BUDGET, "built-in"
    return result


_TOP_LEVEL_KEY = re.compile(r"^[A-Za-z_][\w-]*\s*:")


def write_setting(path: Path, *, mode: str | None = None, budget: int | None = None) -> dict:
    """Set ``sentinel.mode`` and/or ``sentinel.max_act_without_gate`` in a config file, leaving every other line as it was.

    Only the top-level ``sentinel:`` block is rewritten (comments elsewhere survive, which a YAML round trip would drop). Values
    already in the block that are not being set are kept. Returns the two settings as written.
    """
    if mode is not None and not _valid_mode(mode):
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    if budget is not None and _valid_budget(budget) is None:
        raise ValueError(f"max-act-without-gate must be a whole number from 0 to {BUDGET_MAX}")
    existing = _sentinel_block(path)
    block: dict = {}
    if _valid_mode(existing.get("mode")) is not None:  # a bare `off` reads back as False and is kept as off
        block["mode"] = _valid_mode(existing.get("mode"))
    if _valid_budget(existing.get("max_act_without_gate")) is not None:
        block["max_act_without_gate"] = _valid_budget(existing.get("max_act_without_gate"))
    extra = {k: v for k, v in existing.items() if k not in ("mode", "max_act_without_gate")}
    if mode is not None:
        block["mode"] = _valid_mode(mode)
    if budget is not None:
        block["max_act_without_gate"] = _valid_budget(budget)
    target = Path(os.path.realpath(path))  # write through a symlink, never replace it with a plain file
    lines = target.read_text(encoding="utf-8").splitlines() if target.is_file() else []
    start = next((i for i, line in enumerate(lines) if re.match(r"^sentinel\s*:", line)), None)
    if start is not None:
        # The block is the indented lines right after it; the first blank or unindented line ends it, so nothing after the
        # block (a comment, a quoted or dotted key) is ever deleted.
        end = start + 1
        while end < len(lines) and lines[end].strip() and lines[end][:1] in (" ", "\t"):
            end += 1
        del lines[start:end]
    else:
        if lines and lines[-1].strip():
            lines.append("")
        start = len(lines)
    rendered = ["sentinel:"]
    if "mode" in block:
        rendered.append(f'  mode: "{block["mode"]}"')  # quoted: a bare off would read back as false
    if "max_act_without_gate" in block:
        rendered.append(f"  max_act_without_gate: {block['max_act_without_gate']}")
    rendered += [f"  {k}: {json.dumps(v)}" for k, v in extra.items()]
    lines[start:start] = rendered
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if target.exists():
        os.chmod(tmp, target.stat().st_mode & 0o7777)
    tmp.replace(target)
    return {k: block[k] for k in ("mode", "max_act_without_gate") if k in block}


# ── what an act is ──────────────────────────────────────────────────────────────────────────────────────────────────────


def _decision_record():
    spec = importlib.util.spec_from_file_location(
        "_empirica_decision_record_d", Path(__file__).with_name("decision_record.py")
    )
    if spec is None or spec.loader is None:
        raise ImportError("decision_record.py not found beside sentinel_discipline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _file_paths(dr, tool_name: str, tool_input: dict, cwd: str | None) -> list[str]:
    """The files a file tool writes, absolute: a relative path is read against the call's working directory."""
    if tool_name == "MultiEdit":
        raw = [str(tool_input["file_path"])] if tool_input.get("file_path") else []
    elif tool_name == "NotebookEdit":
        raw = [str(tool_input["notebook_path"])] if tool_input.get("notebook_path") else []
    else:
        raw = dr._written_paths(tool_name, tool_input, cwd)
    base = cwd or os.getcwd()
    return [p if os.path.isabs(os.path.expanduser(p)) else os.path.join(base, p) for p in raw]


def _in_practice(path: str, cwd: str | None, project_root: str | None) -> bool:
    """True when ``path`` (resolved against ``cwd``, symlinks followed) is inside the practice and outside its protected dirs."""
    if not project_root:
        return False
    raw = os.path.expanduser(path)
    full = raw if os.path.isabs(raw) else os.path.join(cwd or os.getcwd(), raw)
    real, root = os.path.realpath(full), os.path.realpath(project_root)
    if real != root and not real.startswith(root + os.sep):
        return False
    return not any(part in _PROTECTED_PARTS for part in Path(os.path.relpath(real, root)).parts)


def _commit_args_ok(args: list[str]) -> str | None:
    """None when every git commit argument is a plain message or a benign flag; else the first that is not."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-m", "--message"):
            i += 2
            continue
        if a.startswith("--message=") or a in ("-a", "--all", "-q", "--quiet", "-s", "--signoff", "-v", "--verbose"):
            i += 1
            continue
        if a.startswith("-") and not a.startswith("--") and set(a[1:]) <= set("aqsvm"):
            i += 2 if a.endswith("m") else 1  # a cluster such as -am takes the message next
            continue
        return a
    return None


def _statement_why(words: list[str], cwd: str | None, project_root: str | None) -> str | None:
    """Why one shell statement is not trivial, or None when it is."""
    program, args = words[0], words[1:]
    if program not in _COMMANDS:
        return f"{program} is not one of the commands discipline mode runs without a transaction (git add/commit/mv/rm, mkdir, touch)"
    if program == "git":
        verb = args[0] if args else ""
        if verb not in _GIT_VERBS:
            return f"git {verb or '(no verb)'} is not one of git add, commit, mv, rm"
        rest = args[1:]
        if verb == "commit":
            bad = _commit_args_ok(rest)
            return f"git commit {bad} is not allowed without a transaction" if bad else None
        allowed_flags = {
            "add": {"-A", "--all", "-u", "--update", "--"},
            "mv": {"--"},
            "rm": {"-q", "--quiet", "--cached", "--"},
        }
        for a in rest:
            if a.startswith("-") and a not in allowed_flags[verb]:
                return f"git {verb} {a} is not allowed without a transaction"
        paths = [a for a in rest if not a.startswith("-")]
    else:
        flags = [a for a in args if a.startswith("-")]
        if any(f not in ({"-p", "--parents"} if program == "mkdir" else set()) for f in flags):
            return f"{program} {flags[0]} is not allowed without a transaction"
        paths = [a for a in args if not a.startswith("-")]
        if not paths:
            return f"{program} names nothing to change"
    outside = [p for p in paths if not _in_practice(p, cwd, project_root)]
    if outside:
        return f"{outside[0]} is outside the practice or in a protected directory"
    return None


def _shell_why(command: str, cwd: str | None, project_root: str | None) -> str | None:
    if any(marker in command for marker in ("$(", "`", "<<")):
        return "what it changes cannot be read from its text"
    try:
        lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return "its text does not parse"
    statements: list[list[str]] = [[]]
    for token in tokens:
        if token in ("&&", ";"):
            statements.append([])
        elif token and set(token) <= _PUNCTUATION:
            return f"it uses {token!r} (a redirect, pipe or background job)"
        else:
            statements[-1].append(token)
    statements = [st for st in statements if st]
    if not statements:
        return "it changes nothing"
    if not _in_practice(cwd or os.getcwd(), None, project_root) and os.path.realpath(
        cwd or os.getcwd()
    ) != os.path.realpath(project_root or "/nonexistent"):
        return "its working directory is outside the practice"
    for words in statements:
        why = _statement_why(words, cwd, project_root)
        if why:
            return why
    return None


def judge(tool_name: str, tool_input, *, cwd: str | None, project_root: str | None, empirica_is_read=None) -> dict:
    """Whether this call may run as an out-of-loop act: {"trivial": bool, "classes": [...], "why": str}.

    An allowlist: a file edit inside the practice (symlinks followed, protected dirs excluded), or a shell command made only of
    git add/commit/mv/rm, mkdir and touch on paths inside the practice, joined by && or ;. ``empirica_is_read`` is accepted for
    the caller's signature and unused."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if tool_name in _FILE_TOOLS:
        paths = _file_paths(_decision_record(), tool_name, tool_input, cwd)
        if not paths:
            return {"trivial": False, "classes": ["unknown"], "why": "the file it writes cannot be read from the call"}
        outside = [p for p in paths if not _in_practice(p, cwd, project_root)]
        if outside:
            return {
                "trivial": False,
                "classes": ["outside_write"],
                "why": f"{outside[0]} is outside the practice or in a protected directory",
            }
        return {"trivial": True, "classes": ["project_write"], "why": ""}
    if tool_name == "Bash":
        command = str(tool_input.get("command") or "")
        why = _shell_why(command, cwd, project_root)
        if why:
            return {"trivial": False, "classes": ["unknown"], "why": why}
        return {
            "trivial": True,
            "classes": ["vcs_local" if command.lstrip().startswith("git") else "project_write"],
            "why": "",
        }
    return {"trivial": False, "classes": ["external_tool"], "why": f"{tool_name} is not a file edit or a shell command"}


# ── plan-shaped work: no budget ────────────────────────────────────────────────────────────────────────────────────────
#
# Work that is being planned is not trivial, however small its first edit. Two signals, both since the person's latest prompt:
# the prompt asked for planned or multi-step work (context-shift-tracker classifies it on UserPromptSubmit and keeps the flag
# below), or the session entered plan mode or laid out tasks (two or more TaskCreate calls, or a TodoWrite of two or more items),
# read from the transcript's tail. Either one sends the act to a transaction.

_TRANSCRIPT_TAIL = 512 * 1024


def plan_flag_path(suffix: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", suffix or "")
    return Path.home() / ".empirica" / f"plan_shaped{safe}.json"


def mark_plan_shaped(suffix: str, prompt_is_plan_shaped: bool) -> None:
    """Set or clear the prompt signal; context-shift-tracker calls this on every prompt. Never raises."""
    path = plan_flag_path(suffix)
    try:
        if prompt_is_plan_shaped:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"ts": round(time.time(), 3)}), encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def _is_person_prompt(record: dict) -> bool:
    """A user record that is the person's prompt, not a tool result Claude Code also files under `user`."""
    if record.get("type") != "user":
        return False
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        kinds = {c.get("type") for c in content if isinstance(c, dict)}
        return "tool_result" not in kinds and "text" in kinds
    return False


def planning_in_transcript(transcript_path: str | None) -> str | None:
    """What in the transcript since the person's latest prompt makes this plan-shaped, or None."""
    if not transcript_path:
        return None
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - _TRANSCRIPT_TAIL))
            lines = fh.read().split(b"\n")
    except OSError:
        return None
    task_creates = 0
    for raw in reversed(lines):
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        if _is_person_prompt(record):
            break
        content = (record.get("message") or {}).get("content") if record.get("type") == "assistant" else None
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict) or item.get("type") != "tool_use":
                continue
            name = item.get("name")
            if name == "EnterPlanMode":
                return "the session entered plan mode"
            if name == "TaskCreate":
                task_creates += 1
            todos = (item.get("input") or {}).get("todos") if name == "TodoWrite" else None
            if isinstance(todos, list) and len(todos) >= 2:
                return f"the session laid out {len(todos)} todos"
    if task_creates >= 2:
        return f"the session created {task_creates} tasks"
    return None


def plan_shaped(suffix: str, transcript_path: str | None) -> str | None:
    if plan_flag_path(suffix).exists():
        return "the latest prompt asks for planned or multi-step work"
    return planning_in_transcript(transcript_path)


# ── the out-of-loop ledger ──────────────────────────────────────────────────────────────────────────────────────────────


def ledger_path(suffix: str) -> Path:
    """One ledger per instance (the suffix the gate's counters use), so two panes do not spend one budget."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", suffix or "")
    return Path.home() / ".empirica" / f"out_of_loop{safe}.jsonl"


def ledger(suffix: str) -> list[dict]:
    try:
        lines = ledger_path(suffix).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def take_ledger(suffix: str) -> list[dict]:
    """The ledger's acts, removed: what PREFLIGHT declares as already changed."""
    entries = ledger(suffix)
    try:
        ledger_path(suffix).unlink()
    except OSError:
        pass
    return entries


def _entry(dr, tool_name: str, tool_input: dict, classes: list[str], cwd: str | None) -> dict:
    entry: dict = {"ts": round(time.time(), 3), "tool": tool_name, "classes": classes}
    if tool_name in _FILE_TOOLS:
        entry["paths"] = _file_paths(dr, tool_name, tool_input, cwd)
    else:
        entry["command_class"] = dr.command_class(tool_name, tool_input)
        entry["input_sha256"] = dr.input_hash(tool_input)  # the command itself can hold a secret
    return entry


def _describe(entries: list[dict]) -> str:
    return "; ".join(
        ", ".join(e.get("paths") or []) or str(e.get("command_class") or e.get("tool") or "?") for e in entries[-5:]
    )


def decide(
    tool_name: str,
    tool_input,
    *,
    cwd: str | None,
    project_root: str | None,
    suffix: str,
    empirica_is_read,
    transcript_path: str | None = None,
) -> tuple[str, str] | None:
    """The gate's answer for a praxic call outside a transaction, or None for no opinion (strict: refuse as before)."""
    settings = effective(project_root)
    mode, budget = settings["mode"], settings["budget"]
    if mode == "strict":
        return None
    if mode == "off":
        return ("allow", f"Sentinel mode off ({settings['mode_source']}): allowed outside a transaction, not counted.")
    planning = plan_shaped(suffix, transcript_path)
    if planning:
        return ("deny", f"Outside a transaction, and this is planned work: {planning}. Open one with PREFLIGHT.")
    verdict = judge(tool_name, tool_input, cwd=cwd, project_root=project_root, empirica_is_read=empirica_is_read)
    if not verdict["trivial"]:
        return (
            "deny",
            f"Outside a transaction, and not trivial: {verdict['why']}. Open one with PREFLIGHT (discipline mode lets only "
            "project-local changes whose effect is readable from the call run without one).",
        )
    done = ledger(suffix)
    if len(done) >= budget:
        return (
            "deny",
            f"No longer trivial: {len(done)} change(s) already made outside a transaction (budget {budget}, from "
            f"{settings['budget_source']}). PREFLIGHT, declaring what was already changed: {_describe(done)}.",
        )
    dr = _decision_record()
    entry = _entry(dr, tool_name, tool_input if isinstance(tool_input, dict) else {}, verdict["classes"], cwd)
    try:
        path = ledger_path(suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        return (
            "deny",
            "Outside a transaction, and the out-of-loop ledger cannot be written, so this act would go unrecorded.",
        )
    return (
        "allow",
        f"Out of loop, act {len(done) + 1} of {budget} (discipline mode): recorded, and declared at the next PREFLIGHT.",
    )
