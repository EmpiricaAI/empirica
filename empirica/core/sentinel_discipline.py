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

#: Effect classes that always need a transaction, whatever the budget: outward, irreversible, or outside the practice.
ALWAYS_GATED = frozenset(
    {
        "vcs_remote",
        "install",
        "delete_recursive",
        "remote_exec",
        "network_send",
        "credential_write",
        "config_write",
        "outside_write",
        "process",
    }
)
#: Effect classes an out-of-loop act may have.
TRIVIAL = frozenset({"project_write", "empirica_state", "vcs_local"})
#: Programs whose subcommand publishes, and those subcommands.
_PUBLISHERS = {
    "npm": {"publish"},
    "pnpm": {"publish"},
    "yarn": {"publish", "npm"},
    "cargo": {"publish"},
    "twine": {"upload"},
    "gem": {"push"},
    "poetry": {"publish"},
    "uv": {"publish"},
    "flit": {"publish"},
    "hatch": {"publish"},
}
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
    block = dict(_sentinel_block(path))
    if mode is not None:
        block["mode"] = _valid_mode(mode)
    if budget is not None:
        block["max_act_without_gate"] = _valid_budget(budget)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    start = next((i for i, line in enumerate(lines) if re.match(r"^sentinel\s*:", line)), None)
    if start is not None:
        end = next((j for j in range(start + 1, len(lines)) if _TOP_LEVEL_KEY.match(lines[j])), len(lines))
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
    rendered += [f"  {k}: {json.dumps(v)}" for k, v in block.items() if k not in ("mode", "max_act_without_gate")]
    lines[start:start] = rendered
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
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


def _publishes(dr, command: str) -> str | None:
    """The publishing step in a shell command (`git tag v1`, `npm publish`, `twine upload`), or None."""
    for words in dr._statements(command) or []:
        program, args = dr._program_and_args(words)
        positional = [a for a in args if not a.startswith("-")]
        listing = {"-l", "--list", "-n", "--contains", "--no-contains", "--points-at", "--merged", "--no-merged", "-d"}
        if program == "git" and positional[:1] == ["tag"] and len(positional) > 1 and not listing & set(args):
            return "git tag"  # creating a tag; listing (`-l 'v1*'`) and deleting a local one (`-d`) are not publishing
        if program == "gh" and positional[:1] == ["release"]:
            return "gh release"
        verbs = _PUBLISHERS.get(program)
        if verbs and positional[:1] and positional[0] in verbs:
            return f"{program} {positional[0]}"
    return None


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


def judge(tool_name: str, tool_input, *, cwd: str | None, project_root: str | None, empirica_is_read) -> dict:
    """Whether this call may run as an out-of-loop act: {"trivial": bool, "classes": [...], "why": str}."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    dr = _decision_record()
    if tool_name in _FILE_TOOLS:
        paths = _file_paths(dr, tool_name, tool_input, cwd)
        if not paths:
            return {"trivial": False, "classes": ["unknown"], "why": "the file it writes cannot be read from the call"}
        classes = sorted({dr.path_effect(p, project_root) for p in paths})
    elif tool_name == "Bash":
        command = str(tool_input.get("command") or "")
        step = _publishes(dr, command)
        if step:
            return {"trivial": False, "classes": ["vcs_remote"], "why": f"{step} publishes"}
        inferred = dr.infer_shell_effects(
            command, cwd=cwd, project_path=project_root, empirica_is_read=empirica_is_read
        )
        classes = inferred["classes"]
        if not inferred["reliable"]:
            return {"trivial": False, "classes": classes, "why": "what it changes cannot be read from its text"}
    else:
        return {
            "trivial": False,
            "classes": ["external_tool"],
            "why": f"{tool_name} is not a file edit or a shell command",
        }
    gated = sorted(set(classes) & ALWAYS_GATED)
    if gated:
        return {"trivial": False, "classes": classes, "why": f"it changes {', '.join(gated)}"}
    if not set(classes) <= TRIVIAL:
        return {
            "trivial": False,
            "classes": classes,
            "why": f"its effect is {', '.join(sorted(set(classes) - TRIVIAL))}",
        }
    return {"trivial": True, "classes": classes, "why": ""}


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
        return ("allow", f"Sentinel mode off ({settings['mode_source']}): allowed and recorded.")
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
