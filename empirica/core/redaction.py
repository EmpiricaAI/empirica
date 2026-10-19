"""Credential redaction for anything written into the epistemic graph.

Artifacts are *retrieved* — into later sessions, into Qdrant, and (at
``shared``/``public`` visibility) across the org. So any capture path that
stores raw tool input is a credential exfiltration path, and "we invalidated
the row" does not fix it: invalidation removes an artifact from retrieval and
leaves its text in the table.

Measured 2026-07-27: the PostToolUseFailure hook stringified whole ``tool_input``
dicts into ``dead_end.approach``. MCP ``cortex_*`` tools take ``api_key`` as a
parameter, so a live admin credential was written into artifact rows on six
practices — and on this one, into three ``project_findings`` as well, one of
them at ``shared`` visibility.

Two layers, deliberately redundant:

1. :func:`scrub_mapping` drops secret-NAMED keys before a dict is ever
   stringified. This is the primary defense and it is exact.
2. :func:`redact_secrets` regex-scrubs secret-SHAPED tokens from free text.
   This is the backstop for values that arrive somewhere we did not anticipate
   — a token pasted inside a Bash command, an error message echoing a header.

Neither is sufficient alone: (1) cannot see a key inside a shell string, and
(2) cannot know that ``{"foo": "hunter2"}`` is a password. Keep both.
"""

from __future__ import annotations

import re

REDACTED = "<redacted>"

# Key names whose VALUE is a secret regardless of shape. Matched
# case-insensitively against the whole key, and against ``*_<name>`` suffixes
# (``cortex_api_key``, ``user_password``).
SECRET_KEY_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "key",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "session_key",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "pwd",
        "authorization",
        "auth",
        "credential",
        "credentials",
        "private_key",
        "signing_key",
    }
)

# Secret SHAPES. Each pattern keeps its identifying prefix so a reader can still
# tell WHICH credential was involved — that is diagnostically useful and is not
# itself sensitive — while the entropy-bearing tail is destroyed.
_SHAPE_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bctx_[A-Za-z0-9_\-]{8,}"), f"ctx_{REDACTED}"),
    # ntfy access tokens (`tk_` + base32). Found stored raw in a scanner snapshot and on ten listener command lines
    # on 2026-10-15; without this rule only the `Authorization: Bearer tk_...` form was caught, not the bare token.
    (re.compile(r"\btk_[A-Za-z0-9]{20,}"), f"tk_{REDACTED}"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), f"sk-{REDACTED}"),
    (re.compile(r"\bghp_[A-Za-z0-9]{16,}"), f"ghp_{REDACTED}"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), f"github_pat_{REDACTED}"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), f"xox-{REDACTED}"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), f"AKIA{REDACTED}"),
    (re.compile(r"\bmst-[A-Za-z0-9]{16,}"), f"mst-{REDACTED}"),
    # JWTs — three base64url segments.
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]+"),
        f"{REDACTED}-jwt",
    ),
    # HTTP auth headers, generalised over the SCHEME.
    #
    # The first cut matched `bearer` only, which cortex correctly called a scheme
    # allowlist wearing the costume of a redactor: `Authorization: token <hex>` is
    # the standard forgejo/gitea form and it walked straight through, in clear, on a
    # practice that runs forgejo. It slipped BOTH layers — no `token` shape rule, and
    # _KV_PATTERN wants a quoted `key: "value"` while a header is
    # `Name: scheme credential` with the secret in third position, unquoted.
    #
    # Matching the header by name instead of by scheme means Basic / ApiKey / Token /
    # whatever-comes-next need no new rule. The scheme is preserved: it says which
    # credential was involved without carrying the credential.
    (
        re.compile(
            r"(?i)\b(authorization|proxy-authorization)(\s*:\s*)([A-Za-z][A-Za-z0-9_-]*[ \t]+)?[A-Za-z0-9._\-+/=]{8,}"
        ),
        rf"\1\2\3{REDACTED}",
    ),
    # Bare scheme-prefixed forms, for credentials that appear without the header name
    # (a curl -H fragment, an error echoing just the value).
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"), f"Bearer {REDACTED}"),
    (re.compile(r"(?i)\btoken\s+[A-Za-z0-9._\-]{12,}"), f"token {REDACTED}"),
    # Credentials embedded in a URL — `https://user:token@host`. Found while
    # verifying cortex's header report: this is how a git remote carries a forgejo
    # token, so it lands in any captured `git remote add` / `git push` command.
    # Scheme and username survive (both diagnostic); the secret does not.
    (
        re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]*://)([^/\s:@]+):([^/\s@]{4,})@"),
        rf"\1\2:{REDACTED}@",
    ),
)

# ``api_key='...'`` / ``"token": "..."`` / ``--password X`` — a secret-named key
# followed by any value, whatever the value's shape.
_KV_PATTERN = re.compile(
    r"(?i)(['\"]?\b(?:" + "|".join(sorted(SECRET_KEY_NAMES)) + r")\b['\"]?\s*[:=]\s*)(['\"])([^'\"]{4,}?)\2"
)
_FLAG_PATTERN = re.compile(
    r"(?i)(--(?:" + "|".join(sorted(n.replace("_", "-") for n in SECRET_KEY_NAMES)) + r")[= ])(\S{4,})"
)


def is_secret_key(name: str) -> bool:
    """Does this mapping key hold a secret, judged by name alone?"""
    n = str(name).strip().lower().lstrip("-").replace("-", "_")
    if n in SECRET_KEY_NAMES:
        return True
    # ``cortex_api_key`` / ``x_auth_token`` — suffix match on a known name.
    return any(n.endswith("_" + known) for known in SECRET_KEY_NAMES)


def redact_secrets(text: str) -> str:
    """Scrub secret-shaped tokens out of free text. Never raises."""
    if not text:
        return text
    try:
        out = str(text)
        # Key/value forms first: they catch values whose SHAPE is unremarkable
        # (a plain password) and would otherwise survive every shape pattern.
        out = _KV_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}{m.group(2)}", out)
        out = _FLAG_PATTERN.sub(lambda m: f"{m.group(1)}{REDACTED}", out)
        for pattern, replacement in _SHAPE_PATTERNS:
            out = pattern.sub(replacement, out)
        return out
    except Exception:
        # A redactor that throws must not become a redactor that is skipped.
        # Failing closed means: emit nothing rather than emit the secret.
        return REDACTED


def scrub_mapping(data, _depth: int = 0):
    """Recursively replace secret-NAMED values in a mapping before it is stringified.

    Returns a new structure; the input is not mutated. Non-mapping values pass
    through :func:`redact_secrets` so a token nested in a free-text field (a
    ``command`` string, say) is still caught.
    """
    if _depth > 6:
        return data
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if is_secret_key(k):
                out[k] = REDACTED
            else:
                out[k] = scrub_mapping(v, _depth + 1)
        return out
    if isinstance(data, (list, tuple)):
        scrubbed = [scrub_mapping(v, _depth + 1) for v in data]
        return type(data)(scrubbed) if isinstance(data, tuple) else scrubbed
    if isinstance(data, str):
        return redact_secrets(data)
    return data


def contains_secret(text: str) -> bool:
    """Would :func:`redact_secrets` change this text? Used by the scrub migration
    to touch only the rows that need it (and to report an honest count)."""
    if not text:
        return False
    return redact_secrets(text) != str(text)


# ── Storage variant ──────────────────────────────────────────────────────────────────────────────────────────────────
#
# redact_secrets is deliberately broad: it serves a hook scrubbing tool arguments, where an over-redacted word costs nothing.
# Artifact text is knowledge people will read and retrieve, and the broad rules rewrite it: "bearer authentication is the
# scheme" became "Bearer <redacted> is the scheme", "token budget_exhausted" became "token <redacted>", and a quoted JSON
# example `"key": "name"` lost its value. redact_for_storage keeps every strong shape and demands a DIGIT in the values of the
# bare `bearer X` / `token X` / `Authorization: scheme X` forms (a real credential has one; an English word does not), and
# drops the generic key names (key, auth, credential...) from the key/value rules.

_BROAD_BARE = {f"Bearer {REDACTED}", f"token {REDACTED}"}
_HEADER_REPLACEMENT = r"\1\2\3" + REDACTED

# A value only counts as a credential if it LOOKS like one: no placeholder syntax ($ { } < > *), a digit, and enough length.
# The first cut matched on names and prefixes alone and flagged 27 rows in this practice's own store that held code and
# documentation about credentials, not credentials: a `{"Authorization": f"Bearer {token}"}` template, `API_KEY=$(grep ...)`,
# `https://user:<token>@host`, and the bare `ctx_empirica_adm_` prefix.
_VALUE_CHARS = r"[A-Za-z0-9._~+/=\-]"
_STORAGE_OVERRIDES = {
    # ctx_<org>_adm_<32 hex>: demand a real key's length and a digit, so the prefix mentioned in prose is left alone
    f"ctx_{REDACTED}": (re.compile(r"\bctx_(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{24,}"), f"ctx_{REDACTED}"),
}
_URL_CREDENTIALS = (
    re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]*://)([^/\s:@<>${}*]+):(?=[^/\s@]*\d)([A-Za-z0-9._~+\-]{8,})@"),
    rf"\1\2:{REDACTED}@",
)

_STORAGE_SHAPES: tuple[tuple[re.Pattern, str], ...] | None = tuple(
    _STORAGE_OVERRIDES.get(rep, (pat, rep))
    for pat, rep in _SHAPE_PATTERNS
    if rep not in _BROAD_BARE and rep != _HEADER_REPLACEMENT and not rep.endswith(f":{REDACTED}@")
) + (
    _URL_CREDENTIALS,
    (
        re.compile(
            r"(?i)\b(authorization|proxy-authorization)(\s*:\s*)([A-Za-z][A-Za-z0-9_-]*[ \t]+)?"
            rf"(?=[A-Za-z0-9._\-+/=]*\d){_VALUE_CHARS}{{12,}}"
        ),
        _HEADER_REPLACEMENT,
    ),
    (re.compile(r"(?i)\bbearer\s+(?=[A-Za-z0-9._\-]*\d)[A-Za-z0-9._\-]{16,}"), f"Bearer {REDACTED}"),
    (re.compile(r"(?i)\btoken\s+(?=[A-Za-z0-9._\-]*\d)[A-Za-z0-9._\-]{24,}"), f"token {REDACTED}"),
)

_GENERIC_KEY_NAMES = frozenset({"key", "auth", "credential", "credentials", "secret", "token"})
_STORAGE_KEY_NAMES = sorted(SECRET_KEY_NAMES - _GENERIC_KEY_NAMES)
_STORAGE_KV = re.compile(
    r"(?i)(['\"]?\b(?:" + "|".join(_STORAGE_KEY_NAMES) + r")\b['\"]?\s*[:=]\s*)(['\"])([A-Za-z0-9._~+/=\-]{12,})\2"
)
_STORAGE_FLAG = re.compile(
    r"(?i)(--(?:"
    + "|".join(n.replace("_", "-") for n in _STORAGE_KEY_NAMES)
    # The value ends at whitespace or at a closing delimiter. `\S{8,}` took the quote and comma after a value that ended a JSON
    # string, which turned `"ran with --api-key abcdef123456",` into invalid JSON once the offline scrub wrote it back.
    + r")[= ])([^\s\"'`,;)\]}]{8,})"
)


def redact_for_storage(text):
    """Scrub credentials from text about to be STORED as an artifact, leaving ordinary prose untouched. Never raises.

    ``None`` and ``""`` come back unchanged. If a rule fails the answer is the redaction marker, not the text: failing closed.
    """
    if not text:
        return text
    try:
        out = str(text)
        out = _STORAGE_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}{m.group(2)}", out)
        out = _STORAGE_FLAG.sub(lambda m: f"{m.group(1)}{REDACTED}", out)
        for pattern, replacement in _STORAGE_SHAPES:  # type: ignore[union-attr]
            out = pattern.sub(replacement, out)
        return out
    except Exception:
        return REDACTED


#: The free-text keys of an artifact (CLI config, batch node data, repository arguments) that are scrubbed on write.
ARTIFACT_TEXT_FIELDS = (
    "finding",
    "unknown",
    "approach",
    "why_failed",
    "mistake",
    "why_wrong",
    "prevention",
    "assumption",
    "choice",
    "rationale",
    "alternatives",
    "description",
    "cost_estimate",
)


def redact_artifact_fields(data, fields=ARTIFACT_TEXT_FIELDS):
    """A copy of ``data`` with the artifact text fields scrubbed by :func:`redact_for_storage`. Non-strings pass through."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for name in fields:
        value = out.get(name)
        if isinstance(value, str):
            out[name] = redact_for_storage(value)
    return out
