"""Credentials must never reach the epistemic graph.

The PostToolUseFailure hook stringified whole `tool_input` dicts into
`dead_end.approach`. MCP `cortex_*` tools take `api_key` as a parameter, so a
live admin credential was written into artifact rows across six practices —
and artifacts are *retrieved*: into later sessions, into Qdrant, and at
`shared` visibility across the org.

Invalidating those rows does not fix it. Invalidation removes an artifact from
retrieval and leaves its text in the table — cosmetic remediation that looks
examined. The fix has to be at the write path, which is what these pin.

The hook runs standalone (no package imports), so its redactor is a deliberate
copy of `empirica.core.redaction`. `test_hook_copy_matches_canonical` is what
keeps the copy honest.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from empirica.core import redaction

HOOK_PATH = (
    Path(__file__).resolve().parents[1]
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "tool-failure.py"
)


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("tool_failure_hook", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Synthetic credentials, structurally identical to the real thing — a pattern
# that only matches toy input is a pattern that fails in production.
#
# Assembled from fragments rather than written as literals: these are realistic
# enough that GitHub push protection rejected this file when they were inline
# (it flagged the Slack one). Splitting them keeps the test honest without
# parking secret-shaped strings in the repo for every future scanner to re-flag.
# The value under test is the JOINED string, so coverage is unchanged.
def _j(*parts: str) -> str:
    return "".join(parts)


SECRET_CORPUS = [
    _j("ctx_", "empirica_adm_", "9f3a2b7c4d8e1f6a0b5c9d2e"),
    _j("sk-", "proj-", "aBcD3fGh1jKlMnOpQrStUvWxYz012345"),
    _j("ghp_", "16CharsMinimumABCDEFGHIJKLMNOP"),
    _j("github", "_pat_", "11ABCDEFG0abcdefghijklmnopqrstuvwxyz012345"),
    _j("xox", "b-", "1234567890", "-abcdefghijklmno"),
    _j("AKIA", "IOSFODNN7EXAMPLE"),
    _j("eyJhbGciOiJIUzI1NiJ9.", "eyJzdWIiOiIxMjM0NTY3ODkwIn0.", "dQw4w9WgXcQabcdef"),
    _j("Bearer ", "abcdefghijklmnopqrstuvwxyz0123"),
    # The forgejo/gitea header form. Reported by cortex against 1.12.36: it slipped
    # BOTH layers — no `token` shape rule existed, and _KV_PATTERN wants a quoted
    # key:"value" while a header is `Name: scheme credential`, unquoted, secret in
    # third position. Found sitting in real dead-end artifacts on a forgejo practice.
    _j("token ", "bbbbccccddddeeeeffff00001111222233334444"),
]


# Header forms, checked as whole strings because the SCHEME must survive while the
# credential dies — the scheme is what tells a reader which credential was involved.
AUTH_HEADERS = [
    ("Authorization: " + _j("token ", "bbbbccccddddeeeeffff0000111122223333"), "token"),
    ("Authorization: " + _j("Bearer ", "tk_aaaabbbbccccddddeeee"), "Bearer"),
    ("Authorization: " + _j("Basic ", "dXNlcjpwYXNzd29yZGxvbmc="), "Basic"),
    ("Authorization: " + _j("ApiKey ", "abcdefghijklmnopqrstuvwx"), "ApiKey"),
    ("authorization: " + _j("Token ", "ffffeeeeddddccccbbbbaaaa"), "Token"),
]


@pytest.mark.parametrize("header,scheme", AUTH_HEADERS)
def test_auth_header_redacted_for_any_scheme(header, scheme):
    """Matching `bearer` alone was a scheme allowlist wearing the costume of a
    redactor. Matching the header NAME means the next scheme needs no new rule."""
    out = redaction.redact_secrets(header)
    secret = header.split()[-1]
    assert secret not in out, f"{scheme} credential survived: {out}"
    assert scheme.lower() in out.lower(), "the scheme must survive — it is diagnostic, not secret"


@pytest.mark.parametrize("header,scheme", AUTH_HEADERS)
def test_auth_header_hook_copy_matches_canonical(hook, header, scheme):
    assert hook._redact(header) == redaction.redact_secrets(header)


@pytest.mark.parametrize("header,_scheme", AUTH_HEADERS)
def test_auth_header_redaction_is_idempotent(header, _scheme):
    once = redaction.redact_secrets(header)
    assert redaction.redact_secrets(once) == once


@pytest.mark.parametrize("secret", SECRET_CORPUS)
def test_every_known_shape_is_scrubbed(secret):
    out = redaction.redact_secrets(f"call failed with {secret} in the payload")
    assert secret not in out, f"{secret!r} survived redaction"
    assert "<redacted>" in out


@pytest.mark.parametrize("secret", SECRET_CORPUS)
def test_hook_copy_matches_canonical(hook, secret):
    """The hook cannot import the package, so it carries a copy. If someone
    hardens one side only, this fails — which is the entire point of pinning."""
    text = f"mcp__cortex__cortex_propose: {{'api_key': '{secret}'}}"
    assert hook._redact(text) == redaction.redact_secrets(text)


def test_the_actual_leak_shape_from_the_incident():
    """The exact row shape measured in project_dead_ends on 2026-07-27."""
    secret = _j("ctx_", "empirica_adm_", "9f3a2b7c4d8e1f6a")
    leaked = "mcp__cortex__cortex_propose: {'api_key': '" + secret + "', 'title': 'x'}"
    out = redaction.redact_secrets(leaked)
    assert secret not in out
    # The non-secret context must survive — a redactor that eats the whole row
    # destroys the artifact's diagnostic value along with the secret.
    assert "cortex_propose" in out
    assert "title" in out


def test_secret_named_key_with_unremarkable_value():
    """`hunter2` has no recognizable shape — only the KEY marks it as secret.
    This is why `scrub_mapping` exists alongside the shape patterns."""
    scrubbed = redaction.scrub_mapping({"password": "hunter2", "user": "david"})
    assert scrubbed["password"] == "<redacted>"  # noqa: S105 — the placeholder, not a credential
    assert scrubbed["user"] == "david", "must not redact non-secret fields"


def test_nested_and_suffixed_keys():
    scrubbed = redaction.scrub_mapping({"outer": {"cortex_api_key": "ctx_abcdefghijkl", "note": "fine"}})
    assert scrubbed["outer"]["cortex_api_key"] == "<redacted>"
    assert scrubbed["outer"]["note"] == "fine"


def test_token_inside_a_bash_command():
    """`scrub_mapping` cannot see this one — it is inside a free-text string, so
    the shape patterns are the only thing standing between it and the graph."""
    token = _j("abcdefghijkl", "mnopqrstuvwx")
    cmd = "curl -H 'Authorization: Bearer " + token + "' https://api.example.com"
    out = redaction.redact_secrets(cmd)
    assert token not in out


def test_credentials_embedded_in_a_url():
    """`https://user:token@host` — how a git remote carries a forgejo token, so it
    lands in any captured git remote/push command. Found while verifying cortex's
    header report; they had not reported this one."""
    token = _j("cccccccccccccccccccc", "dddddddddddddddddddd")
    cmd = f"git remote add origin https://gituser:{token}@forgejo.example.com/x.git"
    out = redaction.redact_secrets(cmd)
    assert token not in out
    # Scheme and username are diagnostic, not secret — they must survive.
    assert "https://" in out and "gituser" in out
    assert "forgejo.example.com" in out


def test_url_without_credentials_is_untouched():
    """The URL rule must not fire on an ordinary URL, or it corrupts every artifact
    that mentions one."""
    clean = "cloned from https://github.com/EmpiricaAI/empirica.git at port 8000"
    assert redaction.redact_secrets(clean) == clean


def test_cli_flag_form():
    out = redaction.redact_secrets("empirica foo --api-key ctx_abcdefghijklmnop --verbose")
    assert "ctx_abcdefghijklmnop" not in out
    assert "--verbose" in out


def test_redaction_is_idempotent():
    """Redacting twice must not mangle the placeholder — the scrub path may run
    over already-scrubbed text."""
    once = redaction.redact_secrets("key ctx_abcdefghijklmnop here")
    assert redaction.redact_secrets(once) == once


def test_clean_text_is_untouched():
    """A redactor that rewrites innocent text would corrupt every artifact it
    touches, which is a worse failure than the one it prevents."""
    clean = "Bash: git commit -m 'fix: resolve the sources pane under-read'"
    assert redaction.redact_secrets(clean) == clean


def test_contains_secret_detects_and_clears():
    assert redaction.contains_secret("ctx_abcdefghijklmnop")
    assert not redaction.contains_secret("just some ordinary prose")
    assert not redaction.contains_secret("")


def test_redactor_fails_closed_on_error(monkeypatch):
    """If the redactor itself throws, it must emit the placeholder — never the
    raw text. A redactor that silently degrades to pass-through is worse than
    no redactor, because callers believe they are protected."""

    class ExplodingPattern:
        def sub(self, *_a, **_k):
            raise RuntimeError("pattern engine exploded")

    monkeypatch.setattr(redaction, "_KV_PATTERN", ExplodingPattern())
    assert redaction.redact_secrets("ctx_abcdefghijklmnop") == "<redacted>"
