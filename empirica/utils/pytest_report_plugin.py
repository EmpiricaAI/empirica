"""pytest plugin: leave a test summary where the post-test collector reads it (goal 561c1aea).

Registered as a `pytest11` entry point, so every pytest run on a machine with Empirica installed loads it. It therefore does nothing unless the
pytest rootdir holds an `.empirica/` directory, and nothing it does can fail or alter a test run: every step is guarded and a write error is
dropped. The report is the runner-agnostic `.empirica/test_report.json`; this plugin is one writer of it, `runner` says which.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

REPORT_NAME = "test_report.json"


def _counts(terminalreporter: Any) -> dict[str, int]:
    stats = getattr(terminalreporter, "stats", {}) or {}

    def n(*keys: str) -> int:
        return sum(len(stats.get(k, [])) for k in keys)

    passed = n("passed")
    failed = n("failed", "error")  # a collection or setup error is a failure to the collector
    return {
        "passed": passed,
        "failed": failed,
        "skipped": n("skipped"),
        "total": passed + failed,  # a skipped test did not run: neither evidence for nor against
        "collected": passed + failed + n("skipped", "deselected"),
    }


def _scope(config: Any) -> str:
    """ "full run", or "partial: ..." naming the selection. A -k or single-file rerun overwrites the report and, with nothing recorded
    about what it selected, would read as the suite (ecodex, nextest). Options that do not select tests leave it a full run."""
    opt = config.option
    parts: list[str] = []
    if getattr(opt, "keyword", ""):
        parts.append(f"-k {opt.keyword}")
    if getattr(opt, "markexpr", ""):
        parts.append(f"-m {opt.markexpr}")
    parts.extend(f"--deselect {d}" for d in getattr(opt, "deselect", None) or [])
    if getattr(opt, "lf", False):
        parts.append("--lf")
    invoked = {str(a) for a in getattr(config.invocation_params, "args", ())}
    parts.extend(a for a in config.args if str(a) in invoked)  # a path the user named, not the rootdir default
    return f"partial: {' '.join(parts)}"[:200] if parts else "full run"


def _head(root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _write_atomically(target: Path, payload: dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".test_report.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def pytest_sessionfinish(session: Any, exitstatus: Any) -> None:
    try:
        config = session.config
        if hasattr(config, "workerinput"):  # an xdist worker sees only its share; the controller writes the whole run
            return
        root = Path(str(config.rootpath))
        if not (root / ".empirica").is_dir():
            return
        reporter = config.pluginmanager.get_plugin("terminalreporter")
        _write_atomically(
            root / ".empirica" / REPORT_NAME,
            {
                "runner": "pytest",
                "summary": _counts(reporter),
                "scope": _scope(config),
                "created": time.time(),
                "head": _head(root),
                "exitcode": int(exitstatus),
                "invocation": [str(a) for a in getattr(config, "invocation_params", None).args]
                if getattr(config, "invocation_params", None)
                else [],
            },
        )
    except Exception:
        return
