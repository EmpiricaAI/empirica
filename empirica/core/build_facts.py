"""What code is THIS process running — measured in the process that is running it.

Version truth (David's standing requirement: the fleet runs the same, current
code). A seat cannot be asked what it runs unless something measures it where it
runs, and the first attempt measured the wrong process: the heartbeat is emitted
by the LISTENER DAEMON, which is a separate, usually newer, process. A daemon
restarted an hour ago reports the build on disk; the session it speaks for may
have been running an older one for eleven days. So the SESSION records its own
build into its presence record, and the daemon forwards that record unchanged.

`source` says which of those two a reader is holding:

- ``in_process``  measured by importing what the caller is running. Authoritative
  for that process.
- ``on_disk``     read from a path without running it. What a fresh process WOULD
  get, which is a different fact.

`digest` is content, not the version string. Measured on one box for one package:
`__version__` 1.8.14, dist-info 1.13.46 and pyproject 1.13.47 over byte-identical
trees — every string misstated the code, each in a different direction. The
digest normalises `__version__` lines before hashing so that correcting a stale
string does not read as a code change.
"""

from __future__ import annotations

import hashlib
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: `__version__ = "..."` is normalised away: the digest answers what the CODE is,
#: and a version string inside it would smuggle the name back into the content.
_VERSION_LINE = re.compile(rb'__version__\s*=\s*["\'][^"\']*["\']')


def package_content_digest(pkg_dir: Path) -> str | None:
    """sha256 over a package's `.py` sources — WHAT the code is, not what it is called.

    Relative paths are part of the hash, so a moved or renamed module counts as a
    change. `__pycache__` and non-`.py` files are ignored. Returns None when the
    directory cannot be read: "could not compare" must never read as "same".
    """
    try:
        files = sorted(p for p in pkg_dir.rglob("*.py") if "__pycache__" not in p.parts)
    except OSError:
        return None
    if not files:
        return None
    h = hashlib.sha256()
    for f in files:
        try:
            body = f.read_bytes()
        except OSError:
            return None
        h.update(str(f.relative_to(pkg_dir)).encode())
        h.update(b"\0")
        h.update(_VERSION_LINE.sub(b"__version__ = <normalised>", body))
        h.update(b"\0")
    return h.hexdigest()


def _code_version() -> str | None:
    """The version string that ships WITH the imported code."""
    try:
        import empirica

        return getattr(empirica, "__version__", None)
    except Exception as exc:
        logger.debug("empirica __version__ unavailable: %s", exc)
        return None


def _dist_version() -> str | None:
    """The version string the ENVIRONMENT's metadata claims for that package."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("empirica")
        except PackageNotFoundError:
            return None
    except Exception as exc:
        logger.debug("empirica dist version unavailable: %s", exc)
        return None


@lru_cache(maxsize=1)
def in_process_build() -> dict[str, Any]:
    """The build THIS process is running: version, content digest and its path.

    Cached for the life of the process, which is the point — the answer is a
    property of what was imported, and re-reading the files later would report a
    change this process is not running. A long session keeps reporting the build
    it started with, which is exactly the fact version truth is missing.
    """
    # BOTH strings, because they disagree and neither is authoritative. The
    # standing fact: an editable install serves code whose own `__version__` has
    # moved on while the environment's dist metadata still names the version it
    # was installed at, so the two honestly disagree and reporting either alone
    # is confidently wrong. (First measured 2026-09-23 on a pipx venv serving an
    # editable checkout; the numbers are deliberately not repeated here — a live
    # value written into a comment ages into a false one. Run
    # `in_process_build()` to see the current pair.)
    #
    # `version` is the string shipped with the imported code, `dist_version` is
    # what the environment claims, and `digest` is the only authority over either.
    code_v, dist_v = _code_version(), _dist_version()
    facts: dict[str, Any] = {
        "source": "in_process",
        "version": code_v,
        "dist_version": dist_v,
        "version_disagrees": bool(code_v and dist_v and code_v != dist_v),
    }
    try:
        import empirica

        pkg = Path(empirica.__file__).resolve().parent
        facts["path"] = str(pkg)
        facts["digest"] = package_content_digest(pkg)
    except Exception as exc:
        logger.debug("in-process build unmeasurable: %s", exc)
        facts.setdefault("path", None)
        facts.setdefault("digest", None)
    return facts


def on_disk_build(pkg_dir: Path) -> dict[str, Any]:
    """The build at a path, which is what a FRESH process there would run."""
    return {
        "source": "on_disk",
        "version": None,
        "dist_version": None,
        "version_disagrees": False,
        "path": str(pkg_dir),
        "digest": package_content_digest(Path(pkg_dir)),
    }
