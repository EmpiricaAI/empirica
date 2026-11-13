"""One appended JSON line per Sentinel PreToolUse decision, in the practice's ``.empirica/decisions.jsonl``.

What it is for: the labels a local decision model learns from (ecodex, prop_35xy4qkfgvcvph6rqpbzpbueey) and the audit trail of
the discipline layer planned for 1.15. Each line says what the gate decided, which stage decided it, the mode and vocabulary the
decision was made in, and what the call was: the tool, the program a shell command runs, and a SHA-256 of the input. The input
itself is never written, because a command or a file body can carry a secret.

What it does not do:

* It is not tamper-proof. The agent whose calls are recorded can edit or delete the file with its own shell. It is a record for
  measurement, not evidence against the agent.
* ``effect_classes`` (what the call changed) is null on the decision line: PreToolUse runs before the call. The post line carries
  it for file edits and for sampled shell calls; ``inferred_effect_classes`` is a prediction from the text, never an effect.
* ``human_override`` is null on every line: no hook receives a person's answer at the harness's prompt. A PermissionRequest hook
  writes a ``permission_request`` line, and ``infer_overrides`` derives the answer from what followed, marked as inferred.
* ``permission_mode`` is the harness's own, as its hook input carries it (null when it does not): the same call carries a different risk under
  a read-only mode than under one that lets it write.
* ``rule`` names the stage of the gate that decided (``noetic-firewall``, ``pipeline``, ...), not the individual check inside it.

Cost: one ``open(..., "a")`` and one write per call, no database, no network. ``EMPIRICA_DECISION_RECORD=0`` turns it off.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import time
from pathlib import Path

# Bumped when a field's meaning or a value's vocabulary changes, so a reader can tell records made under different rules apart.
VOCABULARY_VERSION = "1"

# The transaction rules, which every Sentinel mode applies inside a transaction. A decision discipline mode makes outside one
# (the default since 1.15.0) carries rule "discipline-budget"; the field keeps its meaning, so the vocabulary is unchanged.
MODE = "strict"

MAX_BYTES = 10 * 1024 * 1024
FILE_NAME = "decisions.jsonl"

_OFF = {"0", "false", "no", "off"}
_WRAPPERS = {"sudo", "env", "nice", "nohup", "time", "command", "exec"}
# Flags of a wrapper that take the next word as their value, so that word is not the program.
_WRAPPER_VALUE_FLAGS = {
    "sudo": ("-u", "-g", "-C", "-h", "-p", "-r", "-t", "-U", "-D", "--user", "--group", "--host", "--prompt"),
    "nice": ("-n", "--adjustment"),
    "env": ("-u", "-C", "--unset", "--chdir"),
}


def _sha256_hex(data: bytes) -> str:
    """SHA-256 hex digest without importing hashlib: its OpenSSL binding costs about 1.8 ms, and this runs on every hooked
    call. The interpreter's own implementation (``_sha2`` from 3.12, ``_sha256`` before) gives the same digest."""
    try:
        from _sha2 import sha256  # type: ignore[import-not-found]
    except ImportError:
        try:
            from _sha256 import sha256  # type: ignore[import-not-found,no-redef]
        except ImportError:
            from hashlib import sha256  # type: ignore[no-redef]
    return sha256(data).hexdigest()


def enabled() -> bool:
    return (os.environ.get("EMPIRICA_DECISION_RECORD") or "").strip().lower() not in _OFF


def input_hash(tool_input: object) -> str:
    """SHA-256 of the input as canonical JSON: equal inputs give equal hashes, and the input cannot be read back from it."""
    body = json.dumps(tool_input, sort_keys=True, separators=(",", ":"), default=str)
    return _sha256_hex(body.encode("utf-8", "replace"))


_PROGRAM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


def command_class(tool_name: str, tool_input: object) -> str:
    """The program a shell command runs (``git`` for ``FOO=1 /usr/bin/git push``), or the tool's name for any other tool.

    A first word that is not a program name (``API-KEY=sk-...``, a quoted substitution) is recorded as ``unparsed``: this
    field is written in clear, and the input it came from may hold a secret.
    """
    if tool_name != "Bash" or not isinstance(tool_input, dict):
        return tool_name
    command = str(tool_input.get("command") or "")
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    name, _ = _program_and_args(words)
    if not name:
        return tool_name
    return name if _PROGRAM_NAME.match(name) else "unparsed"


def build(
    *,
    tool_name: str,
    tool_input: object,
    decision: str,
    rule: str,
    tool_use_id: str | None = None,
    claude_session_id: str | None = None,
    transaction_id: str | None = None,
    tx_status: str | None = None,
    harness: str | None = None,
    permission_mode: str | None = None,
    inferred: dict | None = None,
    sampled: bool = False,
    turn_id: str | None = None,
) -> dict:
    return {
        "ts": round(time.time(), 3),
        "vocabulary": VOCABULARY_VERSION,
        "mode": MODE,
        "harness": harness,
        "permission_mode": permission_mode,
        "claude_session_id": claude_session_id,
        "transaction_id": transaction_id,
        "tx_status": tx_status,
        "tool_use_id": tool_use_id,
        "turn_id": turn_id,
        "tool": tool_name,
        "command_class": command_class(tool_name, tool_input),
        "input_sha256": input_hash(tool_input),
        "decision": decision,
        "rule": rule,
        "effect_classes": None,
        "inferred_effect_classes": inferred["classes"] if inferred else None,
        "inferred_reliable": inferred["reliable"] if inferred else None,
        "sampled": sampled,
        "human_override": None,
    }


#: Tools whose allowed call is a change by itself; a shell call counts when its inferred effects are not empty.
CHANGE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"})


def out_of_loop_share(path: Path, since: float | None = None) -> dict:
    """The share of allowed changes made outside a transaction, from a practice's decision record (goal 543fd1c6).

    A change: an allowed call of a tool in CHANGE_TOOLS, or an allowed shell call with inferred effects. Out of loop: rule
    ``discipline-budget``, which the gate sets only when it found no open transaction. ``refused_outside`` counts what the
    budget refused there. ``share`` is None when there were no changes; a missing record says so rather than reading zero."""
    out: dict = {
        "changes": 0,
        "out_of_loop": 0,
        "refused_outside": 0,
        "share": None,
        "by_tool": {"edits": {"in": 0, "out": 0}, "commands": {"in": 0, "out": 0}},
        "since": since,
    }
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        out["note"] = f"no decision record at {path}"
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or "decision" not in row or (since is not None and (row.get("ts") or 0) < since):
            continue
        outside = row.get("rule") == "discipline-budget"
        if row["decision"] != "allow":
            out["refused_outside"] += 1 if outside else 0
            continue
        kind = (
            "edits" if row.get("tool") in CHANGE_TOOLS else "commands" if row.get("inferred_effect_classes") else None
        )
        if kind is None:
            continue
        out["changes"] += 1
        out["out_of_loop"] += 1 if outside else 0
        out["by_tool"][kind]["out" if outside else "in"] += 1
    if out["changes"]:
        out["share"] = round(out["out_of_loop"] / out["changes"], 3)
    return out


def record_path(project_path: str | None) -> Path:
    """The practice's file when the call is in a practice, else the seat's (``~/.empirica``), so no decision goes unrecorded."""
    if project_path:
        return Path(project_path) / ".empirica" / FILE_NAME
    return Path.home() / ".empirica" / FILE_NAME


def append(record: dict, path: Path, *, max_bytes: int = MAX_BYTES) -> bool:
    """Append one line. True when it was written; False when it could not be, never an exception on the tool path.

    A file at or over ``max_bytes`` is moved to ``decisions.jsonl.1`` first (one backup, replaced each time), so the record is
    bounded at about twice the cap.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.stat().st_size >= max_bytes:
                os.replace(path, path.with_name(path.name + ".1"))
        except FileNotFoundError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
        return True
    except OSError:
        return False


# ── the outcome half: one "post" line per call, paired with its decision on tool_use_id ─────────────────────────────────

# The hooks that write it, each carrying the same loader (tests/test_decision_record.py keeps the copies identical).
OUTCOME_HOOKS = ("entity-extractor.py", "truncation-legibility.py", "tool-failure.py")
# Every hook that loads this module by its file (the three above and the gate): importing it through the package runs
# empirica/core's init, which the gate's read path never needed and which cost it about 12 ms a call.
LOADER_HOOKS = (*OUTCOME_HOOKS, "sentinel-gate.py", "pane-waiting.py")

# What a call changed. Draft, checked by ecodex (prop_2zqpvjdjwfbghgbi6f2rjtghgm). Only the four path classes are produced so
# far, for Edit, Write and codex's apply_patch; a shell call is recorded with no class and observed false until the before-and-after comparison exists.
EFFECT_CLASSES = (
    "none",
    "project_write",
    "outside_write",
    "config_write",
    "empirica_state",
    "vcs_local",
    "vcs_remote",
    "network_send",
    "install",
    "process",
    "delete_recursive",
    "credential_write",
    "remote_exec",
    "external_tool",
    "unknown",
)

_CONFIG_DIRS = (".claude", ".codex", ".config")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def path_effect(file_path: str, project_path: str | None) -> str:
    """The effect class of writing ``file_path``: the practice's own state and the seat's are ``empirica_state``, harness and
    shell configuration is ``config_write``, anything else in the practice is ``project_write``, the rest ``outside_write``."""
    path = Path(os.path.abspath(os.path.expanduser(file_path)))
    home = Path(os.path.abspath(os.path.expanduser("~")))
    project = Path(os.path.abspath(project_path)) if project_path else None
    if _inside(path, home / ".empirica") or (project is not None and _inside(path, project / ".empirica")):
        return "empirica_state"
    if any(_inside(path, home / d) for d in _CONFIG_DIRS) or (path.parent == home and path.name.startswith(".")):
        return "config_write"
    if project is not None and _inside(path, project):
        return "project_write"
    return "outside_write"


def project_path_for(claude_session_id: str | None, cwd: str | None) -> str | None:
    """The practice a call belongs to: the session's active work, else the working directory when it is a practice root."""
    if claude_session_id:
        try:
            data = json.loads((Path.home() / ".empirica" / f"active_work_{claude_session_id}.json").read_text())
            if data.get("project_path"):
                return str(data["project_path"])
        except (OSError, ValueError, AttributeError):
            pass
    if cwd and (Path(cwd) / ".empirica" / "project.yaml").is_file():
        return str(cwd)
    return None


_PATCH_HEADERS = ("*** Add File: ", "*** Delete File: ", "*** Update File: ", "*** Move to: ")


def patch_lines(patch: str) -> list[str]:
    """A patch's lines as codex reads them: split on newline only, a trailing carriage return dropped, then trimmed.

    Codex trims every line before matching a header and splits only on \\n (codex-rs/apply-patch/src/streaming_parser.rs,
    push_delta and process_line). ``str.splitlines`` also splits on \\x1c, \\x85, \\u2028 and others, and matching untrimmed
    lines missed an indented header: both let a delete or a write outside the practice pass as a small edit (1.16.0
    sweep). Trimming can over-match a content line that looks like a header, which only makes the check stricter."""
    return [line.removesuffix("\r").strip() for line in (patch or "").split("\n")]


def patch_paths(patch: str) -> list[str]:
    """Every path a codex ``apply_patch`` names, in order: added, deleted, updated, and the destination of a move.

    The grammar is codex's own (codex-rs/apply-patch/src/parser.rs, as ecodex cited it): file headers between
    ``*** Begin Patch`` and ``*** End Patch``. Text that does not begin a patch names nothing.
    """
    lines = patch_lines(patch)
    if not lines or lines[0] != "*** Begin Patch":
        return []
    paths: list[str] = []
    # Every header line counts, not only those before the first `*** End Patch`: inside an Update hunk codex trims only the
    # line's end, so an indented `*** End Patch` is a context line and the hunks after it still apply (1.16.0 sweep, pass
    # 2). A content line that reads as a header adds a path, which only makes the check stricter.
    for line in lines[1:]:
        for header in _PATCH_HEADERS:
            if line.startswith(header):
                path = line[len(header) :].strip()
                if path:
                    paths.append(path)
                break
    return paths


def _written_paths(tool_name: str, tool_input: object, cwd: str | None) -> list[str]:
    """The files an Edit, Write or apply_patch call wrote to, absolute. A patch's relative paths are read against ``cwd``."""
    if not isinstance(tool_input, dict):
        return []
    if tool_name in ("Edit", "Write"):
        return [str(tool_input["file_path"])] if tool_input.get("file_path") else []
    if tool_name == "apply_patch":
        base = cwd or os.getcwd()
        return [
            p if os.path.isabs(p) else os.path.join(base, p) for p in patch_paths(str(tool_input.get("command") or ""))
        ]
    return []


def build_outcome(hook_input: dict, outcome: str, project_path: str | None) -> dict:
    """The post line. ``effect_classes`` is the sorted set of classes of the places a successful file edit wrote; null for a
    shell command (not observed yet), for any other tool, and for a call that did not succeed."""
    tool_name = str(hook_input.get("tool_name") or "unknown")
    tool_input = hook_input.get("tool_input") or {}
    effects = None
    scope = None
    sampled_call = False
    if tool_name == "Bash":
        after = _take_after(hook_input.get("tool_use_id"))
        if after is not None:
            effects, scope, sampled_call = after[0], list(OBSERVATION_SCOPE), True
    elif outcome == "success":
        paths = _written_paths(tool_name, tool_input, hook_input.get("cwd"))
        if paths:
            effects = sorted({path_effect(path, project_path) for path in paths})
    return {
        "ts": round(time.time(), 3),
        "vocabulary": VOCABULARY_VERSION,
        "event": "post",
        "harness": (os.environ.get("EMPIRICA_HARNESS") or "claude-code").strip().lower() or "claude-code",
        "claude_session_id": hook_input.get("session_id"),
        "tool_use_id": hook_input.get("tool_use_id"),
        "turn_id": hook_input.get("turn_id"),
        "tool": tool_name,
        "input_sha256": input_hash(tool_input),
        "outcome": outcome,
        "effect_classes": effects,
        "observed": effects is not None,
        "sampled": sampled_call,
        "observation_scope": scope,
    }


def record_outcome(hook_input: dict, outcome: str) -> bool:
    """Append the post line for this call. ``outcome`` is success, failure or interrupted. Never raises."""
    try:
        if not enabled() or not isinstance(hook_input, dict):
            return False
        project = project_path_for(hook_input.get("session_id"), hook_input.get("cwd"))
        return append(build_outcome(hook_input, outcome, project), record_path(project))
    except Exception:
        return False


# ── what a shell command will change, inferred from its text (ecodex, prop_4avo25wpczfcpmv7ug5fbztztq) ────────────────────
#
# A prediction from the text, recorded apart from any observation: features and an audit trail, never a label to train or
# evaluate a decision model on, because a model trained on it learns this classifier. `reliable` is false where the text cannot
# settle it (code run by an interpreter, a substitution, a heredoc): those are the calls a sampled before-and-after comparison
# must measure.

_SEPARATORS = {";", "&&", "||", "|", "&", "|&"}
_REDIRECTS = {">", ">>", "&>", "&>>", ">|"}
_INTERPRETERS = {
    "python",
    "python3",
    "node",
    "deno",
    "bash",
    "sh",
    "zsh",
    "perl",
    "ruby",
    "pytest",
    "eval",
    "source",
    ".",
}
_READ_PROGRAMS = {
    "cat", "bat", "less", "more", "head", "tail", "wc", "ls", "tree", "grep", "rg", "ag", "fd", "jq", "yq", "gron", "awk",
    "uniq", "cut", "tr", "nl", "column", "comm", "diff", "cmp", "echo", "printf", "pwd", "which", "type", "whoami", "id",
    "date", "stat", "file", "du", "df", "ps", "free", "uname", "printenv", "true", "false", "sleep", "cd", "test", "[",
    "xxd", "od", "strings", "basename", "dirname", "realpath", "readlink", "tokei", "scc", "pyright", "mypy", "radon",
    "vulture", "pip-audit", "jobs", "wait", "export",
}  # fmt: skip
_PACKAGE_PROGRAMS = {
    "pip",
    "pip3",
    "pipx",
    "npm",
    "pnpm",
    "yarn",
    "cargo",
    "uv",
    "brew",
    "apt",
    "apt-get",
    "dnf",
    "gem",
    "go",
}
_PACKAGE_VERBS = {"install", "uninstall", "add", "remove", "upgrade", "sync", "reinstall"}
_GIT_READS = {
    "status", "log", "diff", "show", "blame", "grep", "rev-parse", "ls-files", "ls-tree", "for-each-ref", "describe",
    "shortlog", "cat-file", "fetch", "rev-list", "merge-base", "check-ignore", "reflog", "help", "version",
}  # fmt: skip
_GH_READS = {"list", "view", "status", "diff", "checks", "watch", "download"}
_SYSTEMCTL_READS = {"status", "show", "list-units", "list-unit-files", "list-timers", "is-active", "is-enabled", "cat"}
_PATH_ARG_PROGRAMS = {"rm", "mv", "touch", "mkdir", "tee", "truncate", "rmdir", "unlink"}


def _statements(command: str) -> list[list[str]] | None:
    """The command's statements as word lists, split on ; && || | &; None when the text does not tokenise."""
    try:
        lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    out: list[list[str]] = [[]]
    for token in tokens:
        if token in _SEPARATORS:
            out.append([])
        else:
            out[-1].append(token)
    return [s for s in out if s]


def _program_and_args(words: list[str]) -> tuple[str, list[str]]:
    """Skip environment assignments and wrappers (sudo, env, nice, timeout N, ...) to the program that runs."""
    i = 0
    while i < len(words):
        word = words[i]
        if "=" in word and word.split("=", 1)[0].isidentifier():
            i += 1
            continue
        if "=" in word:
            return "unparsed", words[
                i + 1 :
            ]  # `API-KEY=sk/...`: not an assignment, and its tail must not be taken for a name
        name = os.path.basename(word)
        if name in _WRAPPERS:
            i += 1
            while i < len(words) and words[i].startswith("-"):
                takes_value = words[i] in _WRAPPER_VALUE_FLAGS.get(name, ())
                i += 2 if takes_value else 1
            continue
        if name == "timeout":
            i += 2  # timeout DURATION
            continue
        return name, words[i + 1 :]
    return "", []


def _positional(args: list[str]) -> list[str]:
    return [a for a in args if not a.startswith("-")]


#: For the git verbs that list or change depending on their arguments: the subcommands that only read. A bare ``git stash`` is
#: not here because it saves and resets the working tree; ``branch`` and ``tag`` have no read subcommands (``git branch list``
#: creates a branch named "list").
_GIT_READ_SUBCOMMANDS = {
    "stash": {"list", "show"},
    "worktree": {"list"},
    "notes": {"list", "show"},
    "remote": {"show", "get-url"},
}
#: For branch and tag, a listing is a call with no names at all, or one that asks for the list by flag. The same letter means
#: different things per verb (``git branch -a`` lists all, ``git tag -a`` annotates), so the flags are per verb.
_GIT_LISTING_FLAGS = {
    "branch": {"-l", "--list", "--show-current"},
    "tag": {"-l", "--list"},
}
_GIT_BRANCH_WRITE_FLAGS = {
    "-d",
    "-D",
    "--delete",
    "-m",
    "-M",
    "--move",
    "-c",
    "-C",
    "--copy",
    "-u",
    "--set-upstream-to",
}


def _git(args: list[str]) -> set[str]:
    rest = list(args)
    while rest and rest[0].startswith("-"):
        flag = rest.pop(0)
        if flag in ("-C", "-c", "--git-dir", "--work-tree") and rest:
            rest.pop(0)
    verb = rest[0] if rest else ""
    tail = rest[1:]
    if not verb or verb in _GIT_READS:
        return set()
    if verb == "push":
        return {"vcs_remote"}
    if verb in ("branch", "tag"):
        writes = verb == "branch" and any(a in _GIT_BRANCH_WRITE_FLAGS for a in tail)
        if not writes and (not _positional(tail) or any(a in _GIT_LISTING_FLAGS[verb] for a in tail)):
            return set()
        return {"vcs_local"}
    if verb in _GIT_READ_SUBCOMMANDS:
        sub = _positional(tail)[:1]
        if (sub and sub[0] in _GIT_READ_SUBCOMMANDS[verb]) or (not tail and verb != "stash"):
            return set()
        if verb == "remote" and tail and not sub and all(a in ("-v", "--verbose") for a in tail):
            return set()
        return {"vcs_local"}
    if verb == "config" and any(a.startswith("--get") or a == "--list" or a == "-l" for a in tail):
        return set()
    return {"vcs_local"}


def _flags(args: list[str], short_values: str, long_values: set[str]) -> list[tuple[str, str]]:
    """The flags in ``args`` as (name, value) pairs, in every spelling a CLI accepts: ``--name value``, ``--name=value``,
    ``-Xvalue``, and short flags clustered before one that takes a value (``-sXPOST``). ``short_values`` and
    ``long_values`` name the flags that take a value; a value given as the next word is consumed, not read as a flag."""
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(args):
        arg = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else ""
        i += 1
        if arg.startswith("--"):
            name, eq, value = arg.partition("=")
            if not eq and name in long_values:
                value, i = nxt, i + 1
            out.append((name, value))
        elif arg.startswith("-") and len(arg) > 1:
            for j, letter in enumerate(arg[1:], start=1):
                if letter in short_values:
                    value = arg[j + 1 :]
                    if not value:
                        value, i = nxt, i + 1
                    out.append(("-" + letter, value))
                    break
                out.append(("-" + letter, ""))
    return out


_GH_API_LONG_VALUES = {
    "--method", "--field", "--raw-field", "--input", "--header", "--jq", "--template", "--preview", "--cache", "--hostname",
}  # fmt: skip


def _gh(args: list[str]) -> set[str]:
    words = _positional(args)
    if not words:
        return set()
    if words[0] == "api":
        method, sends = "", False
        for name, value in _flags(args, "XfFHqtp", _GH_API_LONG_VALUES):
            if name in ("-X", "--method"):
                method = value.upper()
            elif name in ("-f", "-F", "--field", "--raw-field", "--input"):
                sends = True  # gh api defaults to POST once a field or body is given
        method = method or ("POST" if sends else "GET")
        return set() if method in ("GET", "HEAD") else {"network_send"}
    if words[0] == "auth" or (len(words) > 1 and words[1] in _GH_READS):
        return set()
    return {"vcs_remote"}


_CURL_SENDING = {
    "-d",
    "--data",
    "--data-raw",
    "--data-binary",
    "--data-urlencode",
    "-F",
    "--form",
    "-T",
    "--upload-file",
}
_CURL_SENDING |= {"--json", "--post-data", "--post-file", "--body-data", "--body-file"}  # the last four are wget's
#: curl's and wget's short flags that take a value, so a value is never read as a flag cluster (``-H 'X-Mode: d'``).
_CURL_SHORT_VALUES = "dFTXHuoAeEbcrwxyYzKmCUPQtD"
_CURL_LONG_VALUES = _CURL_SENDING | {
    "--request",
    "--method",
    "--header",
    "--user",
    "--output",
    "--user-agent",
    "--referer",
}


def _curl(program: str, args: list[str]) -> set[str]:
    if program == "wget":
        short_values, sending = "OoaAPeUTtwYQX", {"--post-data", "--post-file", "--body-data", "--body-file"}
        method_flags: tuple[str, ...] = ("--method",)  # wget's -X excludes directories
    else:
        short_values, sending, method_flags = _CURL_SHORT_VALUES, _CURL_SENDING, ("-X", "--request", "--method")
    for name, value in _flags(args, short_values, _CURL_LONG_VALUES):
        if name in sending:
            return {"network_send"}
        if name in method_flags and value.upper() not in ("GET", "HEAD"):
            return {"network_send"}
    return set()


def _paths_written(program: str, args: list[str], cwd: str | None, project_path: str | None) -> set[str]:
    targets = _positional(args)
    if program in ("cp", "ln", "install"):
        targets = targets[-1:]
    elif program in ("chmod", "chown"):
        targets = targets[1:]
    elif program == "sed":
        targets = targets[-1:]
    base = cwd or os.getcwd()
    return {
        path_effect(t if os.path.isabs(os.path.expanduser(t)) else os.path.join(base, t), project_path) for t in targets
    }


def _classify_statement(
    program: str, args: list[str], cwd: str | None, project_path: str | None, empirica_is_read
) -> tuple[set[str], bool]:
    """(classes, reliable) for one statement."""
    if program in _INTERPRETERS:
        if program.startswith("python") and args[:3] in (["-m", "pip", "install"], ["-m", "pip", "uninstall"]):
            return {"install"}, True
        return {"unknown"}, False
    if program in _READ_PROGRAMS or program in ("sort", "sed", "find", "ruff", "yq"):
        return _read_shaped(program, args, cwd, project_path)
    if program == "git":
        return _git(args), True
    if program == "gh":
        return _gh(args), True
    if program in _PACKAGE_PROGRAMS:
        verbs = set(_positional(args)[:2])
        return ({"install"} if verbs & _PACKAGE_VERBS else {"unknown"}), True
    if program in ("curl", "wget"):
        return _curl(program, args), True
    return _classify_other(program, args, cwd, project_path, empirica_is_read)


def _classify_other(
    program: str, args: list[str], cwd: str | None, project_path: str | None, empirica_is_read
) -> tuple[set[str], bool]:
    """Remote, process, empirica and file-writing programs; anything else is unknown."""
    if program == "ssh":
        return {"remote_exec"}, True
    if program in ("scp", "rsync"):
        remote = any(":" in a and not a.startswith(("/", ".", "~")) for a in _positional(args))
        return ({"network_send"} if remote else _paths_written("cp", args, cwd, project_path)), True
    if program in ("systemctl", "service", "launchctl"):
        verbs = set(_positional(args))
        return (set() if verbs & (_SYSTEMCTL_READS | {"list"}) else {"process"}), True
    if program in ("kill", "pkill", "killall"):
        return {"process"}, True
    if program == "empirica":
        words = _positional(args)
        if not words or empirica_is_read("empirica " + " ".join(words[:2])):
            return set(), True
        return {"empirica_state"}, True
    if program == "rm":
        recursive = any(
            a == "--recursive" or (a.startswith("-") and not a.startswith("--") and "r" in a.lower()) for a in args
        )
        return _paths_written(program, args, cwd, project_path) | ({"delete_recursive"} if recursive else set()), True
    if program in _PATH_ARG_PROGRAMS or program in ("cp", "ln", "chmod", "chown", "install"):
        return _paths_written(program, args, cwd, project_path), True
    return {"unknown"}, True


def _read_shaped(program: str, args: list[str], cwd: str | None, project_path: str | None) -> tuple[set[str], bool]:
    """The reading programs and the ones that read unless a flag makes them write."""
    if program == "sed" and any(a == "-i" or a.startswith(("-i", "--in-place")) for a in args):
        return _paths_written("sed", args, cwd, project_path), True
    if program == "find":
        if "-delete" in args:
            return {"delete_recursive"}, True
        if any(a in ("-exec", "-execdir", "-ok") for a in args):
            return {"unknown"}, True
    if program == "sort" and any(a == "-o" or a.startswith("--output") for a in args):
        return {"unknown"}, True
    if program == "awk" and any("system(" in a or ">" in a or "|" in a for a in args):
        return {"unknown"}, False
    if program == "yq" and any(a == "-i" or a.startswith("--inplace") for a in args):
        return {"unknown"}, False
    if program == "fd" and any(a in ("-x", "-X", "--exec", "--exec-batch") for a in args):
        return {"unknown"}, False
    if program == "ruff":
        writes = ("format" in args and "--check" not in args) or "--fix" in args
        return ({"project_write"} if writes else set()), True
    return set(), True


def infer_shell_effects(command: str, *, cwd: str | None, project_path: str | None, empirica_is_read) -> dict:
    """{"classes": sorted inferred classes, "reliable": whether the text can settle it}. ``empirica_is_read`` answers
    whether an ``empirica <verb>`` only reads (the gate passes its own is_read_shaped_empirica_verb)."""
    text = command or ""
    reliable = not any(marker in text for marker in ("$(", "`", "<<"))
    statements = _statements(text)
    if statements is None:
        return {"classes": ["unknown"], "reliable": False}
    classes: set[str] = set()
    for words in statements:
        kept: list[str] = []
        i = 0
        while i < len(words):
            if words[i] in _REDIRECTS and i + 1 < len(words):
                target = words[i + 1]
                if target != "/dev/null" and not target.startswith("&"):
                    classes |= _paths_written("tee", [target], cwd, project_path)
                i += 2
                continue
            kept.append(words[i])
            i += 1
        program, args = _program_and_args(kept)
        if not program:
            continue
        found, ok = _classify_statement(program, args, cwd, project_path, empirica_is_read)
        classes |= found
        reliable = reliable and ok and "unknown" not in found
    return {"classes": sorted(classes), "reliable": reliable}


# ── part A: observe what a sampled shell call changed (ecodex, prop_4avo25wpczfcpmv7ug5fbztztq) ───────────────────────────
#
# A snapshot before the call (PreToolUse, in the gate) and after it (PostToolUse or PostToolUseFailure), paired on tool_use_id
# through a small file. About 15 ms a snapshot on core's repo, so only sampled calls pay it. The view is deliberately narrow and
# the post line names it: the hooks themselves write to ~/.empirica and ~/.claude on every call, so scanning those would report
# a change on every call.

OBSERVATION_SCOPE = ("practice_git_status", "practice_git_refs", "config_files")
SAMPLE_ONE_IN = 10
_CONFIG_FILES = (
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/CLAUDE.md",
    ".codex/config.toml",
    ".bashrc",
    ".zshrc",
    ".profile",
    ".bash_profile",
    ".gitconfig",
    ".tmux.conf",
)
_SNAPSHOT_MAX_AGE = 24 * 3600


def sampled(tool_use_id: str | None, *, reliable: bool | None) -> bool:
    """Every call whose text cannot settle its effect, and a deterministic 1 in SAMPLE_ONE_IN of the rest by id hash."""
    if not tool_use_id:
        return False
    if reliable is False:
        return True
    return int(_sha256_hex(tool_use_id.encode())[:8], 16) % SAMPLE_ONE_IN == 0


def _stat(path: Path) -> list[int] | None:
    try:
        st = path.stat()
        return [st.st_mtime_ns, st.st_size]
    except OSError:
        return None


class SnapshotFailed(Exception):
    """git could not be read. A snapshot that failed is not stored or compared: an empty one would read as a change."""


_GIT_TIMEOUT = 2  # seconds per git call; the gate hook's own harness timeout is 10


def snapshot(project_path: str) -> dict:
    """What the practice's git and the config files look like now. Never rewrites the index (GIT_OPTIONAL_LOCKS=0).

    Paths under ``.empirica/`` are left out: the hooks themselves write there on every call (this record, the transaction
    file, hook counters), so in a practice that tracks the directory they would show as a change on every sampled call.
    Raises SnapshotFailed when git cannot be read.
    """
    import subprocess

    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    root = Path(project_path)

    def git(*args: str) -> str:
        try:
            done = subprocess.run(
                ["git", *args], cwd=root, capture_output=True, text=True, env=env, timeout=_GIT_TIMEOUT, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SnapshotFailed(str(exc)) from exc
        if done.returncode != 0:
            raise SnapshotFailed(done.stderr.strip()[:200])
        return done.stdout

    status: dict[str, list] = {}
    entries = git("status", "--porcelain=v1", "-z").split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) <= 3:
            continue
        code, path = entry[:2], entry[3:]
        paths = [path]
        if ("R" in code or "C" in code) and i < len(entries):
            paths.append(entries[i])  # -z puts a rename's or copy's source path in the next field, whole
            i += 1
        for one in paths:
            if one == ".empirica" or one.startswith(".empirica/"):
                continue
            status[one] = [code, _stat(root / one)]
    refs = {}
    for line in git(
        "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/tags", "refs/stash", "refs/remotes"
    ).splitlines():
        name, _, sha = line.partition(" ")
        refs[name] = sha
    home = Path.home()
    config = {name: _stat(home / name) for name in _CONFIG_FILES}
    return {"status": status, "refs": refs, "config": config}


def observed_effects(before: dict, after: dict, project_path: str | None) -> list[str]:
    """The effect classes between two snapshots, within OBSERVATION_SCOPE."""
    classes: set[str] = set()
    b_status, a_status = before.get("status", {}), after.get("status", {})
    for path in set(b_status) | set(a_status):
        if b_status.get(path) != a_status.get(path):
            classes.add(path_effect(os.path.join(project_path or "", path), project_path))
    b_refs, a_refs = before.get("refs", {}), after.get("refs", {})
    for name in set(b_refs) | set(a_refs):
        if b_refs.get(name) != a_refs.get(name):
            classes.add("vcs_remote" if name.startswith("refs/remotes/") else "vcs_local")
    if before.get("config") != after.get("config"):
        classes.add("config_write")
    return sorted(classes)


def snapshot_path(tool_use_id: str) -> Path:
    safe = _sha256_hex(tool_use_id.encode())[:32]
    return Path.home() / ".empirica" / "decision_snapshots" / f"{safe}.json"


def store_before(tool_use_id: str | None, project_path: str | None, *, decision: str, sampled: bool) -> bool:
    """Take and keep the before-snapshot for an allowed, sampled call in a practice. True when stored."""
    if not (tool_use_id and project_path and sampled and decision == "allow"):
        return False
    try:
        path = snapshot_path(tool_use_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _prune_snapshots(path.parent)
        path.write_text(json.dumps({"project_path": project_path, "snapshot": snapshot(project_path)}))
        return True
    except (OSError, TypeError, ValueError, SnapshotFailed):
        return False


def _take_after(tool_use_id: str | None) -> tuple[list[str], str | None] | None:
    """(observed classes, practice) for a call whose before-snapshot exists; the file is removed. None when not sampled."""
    if not tool_use_id:
        return None
    path = snapshot_path(tool_use_id)
    try:
        stored = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    try:
        path.unlink()
    except OSError:
        pass
    _prune_snapshots(path.parent)
    project = stored.get("project_path")
    if not project:
        return None
    try:
        after = snapshot(project)
    except SnapshotFailed:
        return None
    return observed_effects(stored.get("snapshot") or {}, after, project), project


def _prune_snapshots(directory: Path) -> None:
    """Remove before-snapshots older than a day: a call that never reached its post hook (denied at the prompt, killed)."""
    cutoff = time.time() - _SNAPSHOT_MAX_AGE
    try:
        for entry in os.scandir(directory):
            if entry.name.endswith(".json") and entry.stat().st_mtime < cutoff:
                os.unlink(entry.path)
    except OSError:
        pass


# ── whether a person approved the call: inferred, never observed ───────────────────────────────────────────────────────
#
# A harness's PermissionRequest hook fires when it asks a person to approve a call; codex's carries tool_name, tool_input,
# permission_mode and turn_id, no tool_use_id, and the hook never receives the answer (ecodex, prop_2zqpvjdjwfbghgbi6f2rjtghgm).
# So the hook writes a permission_request line, and infer_overrides reads the answer from what followed, joined on turn_id and
# input_sha256. Every hook line keeps human_override null; the inference is an analysis, and it says it is one.


def record_permission_request(hook_input: dict) -> bool:
    """Append a permission_request line for a harness's PermissionRequest hook. Never raises."""
    try:
        if not enabled() or not isinstance(hook_input, dict):
            return False
        tool_input = hook_input.get("tool_input") or {}
        record = {
            "ts": round(time.time(), 3),
            "vocabulary": VOCABULARY_VERSION,
            "event": "permission_request",
            "harness": (os.environ.get("EMPIRICA_HARNESS") or "claude-code").strip().lower() or "claude-code",
            "claude_session_id": hook_input.get("session_id"),
            "turn_id": hook_input.get("turn_id"),
            "tool": str(hook_input.get("tool_name") or "unknown"),
            "input_sha256": input_hash(tool_input),
            "permission_mode": hook_input.get("permission_mode"),
        }
        project = project_path_for(hook_input.get("session_id"), hook_input.get("cwd"))
        return append(record, record_path(project))
    except Exception:
        return False


def infer_overrides(lines: list[dict]) -> list[dict]:
    """For each permission_request: approved when a successful post line for the same input followed in the same turn, else
    denied_or_aborted. Every answer carries inferred: true."""
    ran = {
        (line.get("turn_id"), line.get("input_sha256"))
        for line in lines
        if line.get("event") == "post" and line.get("outcome") == "success"
    }
    out = []
    for line in lines:
        if line.get("event") != "permission_request":
            continue
        key = (line.get("turn_id"), line.get("input_sha256"))
        out.append(
            {
                "turn_id": key[0],
                "input_sha256": key[1],
                "override": "approved" if key in ran else "denied_or_aborted",
                "inferred": True,
            }
        )
    return out
