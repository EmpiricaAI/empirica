"""`canonical_path` is a LOCATOR, and a locator that only resolves on one box is not one.

`epistemic_sources.canonical_path` was written as `str(Path(p).resolve())` — always
absolute, always machine-specific. Measured on core's own store before this landed:

    absolute, machine-specific   18   (14 inside the repo, 4 under /tmp)
    repo-relative, portable       2
    -------------------------------
    total populated              20

One column holding two incompatible meanings, with 90% in the shape that cannot
travel. That is upstream of any git-as-source-transport policy: a peer who clones
the repo can verify the bytes via `content_hash` and has no way to find the file,
because the only locator names a directory on somebody else's laptop.

**The rule.** A path inside the project root is stored REPO-RELATIVE — it means the
same thing in every clone. A path outside it is stored absolute AND flagged
non-portable, because pretending otherwise is what produced the mess: the value
looked usable and silently was not.

**`content_hash` remains the identity; this is only the locator.** The two answer
different questions — *is this the same content?* and *where do I find it?* — and
conflating them is how a column ends up meaning two things.

The same rule applies one level up, to `entity_registry.source_db`, where 5 rows
carry `sessions:<absolute path>`: a pointer must name a logical thing, never a
filesystem location. That surface is not fixed here.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

#: Marks a stored path as resolvable only on the machine that wrote it. Kept as a
#: separate return value rather than a prefix on the string, so a consumer that
#: ignores it still gets a usable path instead of a mangled one.
NON_PORTABLE = "absolute"
PORTABLE = "repo-relative"


def project_root(explicit: str | Path | None = None) -> Path | None:
    """The directory repo-relative paths are relative TO, or None if unknown.

    Resolution order is deliberate: an explicit argument wins (callers that already
    know their project must not be second-guessed), then the resolver that the rest
    of the codebase uses, then the git root. Returning None when nothing is
    knowable is the honest answer — it makes the caller store an absolute path and
    say so, rather than inventing a root and producing paths relative to nothing.
    """
    if explicit:
        try:
            return Path(explicit).expanduser().resolve()
        except Exception:
            return None
    try:
        from empirica.utils.session_resolver import InstanceResolver

        resolved = InstanceResolver.project_path()
        if resolved:
            return Path(resolved).expanduser().resolve()
    except Exception as e:
        # Fall through to the git root. Logged rather than swallowed: a resolver
        # that is failing every call would otherwise look like "no project here",
        # and every path would silently store absolute.
        logger.debug(f"canonical_path: instance resolver unavailable, trying git root: {e}")
    try:
        from empirica.config.path_resolver import get_git_root

        root = get_git_root()
        return Path(root).resolve() if root else None
    except Exception:
        return None


def normalise(path: str | Path | None, root: str | Path | None = None) -> tuple[str | None, str | None]:
    """Return ``(stored_path, portability)`` for a file-backed source.

    Inside the project root → a POSIX repo-relative string and :data:`PORTABLE`.
    Anywhere else → the absolute path and :data:`NON_PORTABLE`.
    ``(None, None)`` when there is no path at all.

    POSIX separators on purpose: the stored value crosses machines, and a
    backslash-separated path written on Windows would not resolve on the peer that
    reads it — which is the whole defect, one platform along.
    """
    if not path:
        return None, None
    try:
        p = Path(path).expanduser()
        if not p.is_absolute():
            base = project_root(root)
            # A relative input is ALREADY portable if it resolves under the root.
            # Re-anchoring it to cwd (what the old writer did) is what turned
            # portable inputs into machine-specific rows.
            if base is not None and (base / p).exists():
                return p.as_posix(), PORTABLE
            p = Path.cwd() / p
        p = p.resolve()
    except Exception:
        return (str(path), NON_PORTABLE)

    base = project_root(root)
    if base is not None:
        try:
            return p.relative_to(base).as_posix(), PORTABLE
        except ValueError:
            pass  # genuinely outside the repo
    return str(p), NON_PORTABLE


def resolve(stored: str | None, root: str | Path | None = None) -> Path | None:
    """Turn a stored ``canonical_path`` back into a path to open.

    The counterpart every reader needs. Storing repo-relative values without this
    would break source re-fetch and the existence check in `sanctify` — a
    half-migrated column is worse than an un-migrated one, because the rows that
    still work hide the rows that do not.

    Accepts BOTH shapes on purpose: the column holds legacy absolutes and will for
    as long as rows written before this exist, so a reader that only understood the
    new shape would be a second incompatible meaning rather than a fix.
    """
    if not stored:
        return None
    p = Path(stored).expanduser()
    if p.is_absolute():
        return p
    base = project_root(root)
    return (base / p) if base is not None else p
