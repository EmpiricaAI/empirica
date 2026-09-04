"""One credential contract, and the straggler that proved it wasn't shared yet.

`empirica listener on` resolves its ntfy topic from cortex. That resolver read
``api_key`` straight off the config, so after the api_key retirement moved seats
onto daemon-brokered OAuth, an **OAuth-only seat resolved no topic at all** —
arming failed with a message about cortex and topics while the seat was
correctly authenticated the whole time. Reported by mesh-support from a real
box.

The shared helper already existed: `cortex_bearer()` is OAuth-first, falls back
to api_key, and suppresses a key recorded as terminally dead. The defect was a
call site not on it — one of 18 measured.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import empirica.core.cockpit.notification_channels as nc

ROOT = Path(__file__).parent.parent / "empirica"


@pytest.fixture(autouse=True)
def reset_creds_error():
    """`_last_creds_error` is module-global; a leftover from one test would make
    the next one assert against history rather than its own call — the same
    scope-mismatch that put CI red earlier today on the exit-code guard."""
    nc._last_creds_error = None
    yield
    nc._last_creds_error = None


def _stub_bearer(monkeypatch, payload):
    import empirica.core.auth as auth

    monkeypatch.setattr(auth, "cortex_bearer", lambda *a, **k: payload)


def test_an_oauth_only_seat_resolves(monkeypatch):
    """THE regression. No api_key anywhere, a valid OAuth token — the old code
    returned None here and the listener could not arm."""
    _stub_bearer(monkeypatch, {"url": "https://cortex.example", "bearer": "oauth-token", "source": "oauth"})

    assert nc._cortex_creds() == ("https://cortex.example", "oauth-token")
    assert nc.last_credentials_error() is None


def test_an_api_key_seat_still_resolves(monkeypatch):
    """NEGATIVE CONTROL. David's ruling is that keys retire as a DIRECTION — a
    seat with only a key must keep working, so the fallback stays live."""
    _stub_bearer(monkeypatch, {"url": "https://cortex.example", "bearer": "legacy-key", "source": "api_key"})

    assert nc._cortex_creds() == ("https://cortex.example", "legacy-key")


def test_a_seat_with_no_credential_says_so(monkeypatch):
    _stub_bearer(monkeypatch, {"url": "https://cortex.example", "bearer": None, "source": "none"})

    assert nc._cortex_creds() is None
    assert "no cortex credential" in nc.last_credentials_error()


def test_a_SUPPRESSED_credential_does_not_print_as_absent(monkeypatch):
    """A key skipped because it is recorded DEAD is a different state from a seat
    that has none — reporting the first as the second sends an operator to
    provision a credential they already have. `cortex_bearer` supplies the
    reason; this resolver must carry it rather than overwrite it."""
    _stub_bearer(
        monkeypatch,
        {"url": "https://cortex.example", "bearer": None, "source": "none", "reason": "api_key revoked (401 x3)"},
    )

    assert nc._cortex_creds() is None
    assert nc.last_credentials_error() == "api_key revoked (401 x3)"


def test_a_missing_url_is_still_named_separately(monkeypatch):
    _stub_bearer(monkeypatch, {"url": None, "bearer": "tok", "source": "oauth"})

    assert nc._cortex_creds() is None
    assert "url" in nc.last_credentials_error()


def test_resolution_failure_is_reported_not_swallowed(monkeypatch):
    """Fail-soft is the design; fail-SILENT is what the original bug was."""
    import empirica.core.auth as auth

    def _boom(*a, **k):
        raise RuntimeError("token endpoint unreachable")

    monkeypatch.setattr(auth, "cortex_bearer", _boom)

    assert nc._cortex_creds() is None
    assert "token endpoint unreachable" in nc.last_credentials_error()


# ── the scattered contract, measured ─────────────────────────────────────────

#: Modules that legitimately touch `api_key` directly because they OWN it —
#: the loader itself, the login verb that writes it, the migration that moves
#: it, and installers that template config. Everything else asking cortex a
#: question must go through `cortex_bearer`, or it cannot see an OAuth-only
#: seat and cannot see a suppressed key.
CREDENTIAL_OWNERS = {
    "config/credentials_loader.py",
    "cli/command_handlers/auth_commands.py",
    "cli/command_handlers/setup_claude_code.py",
    "core/identity_migration.py",
    "core/auth/cortex_oauth.py",
}


def _inline_api_key_readers() -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for path in sorted(ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        hits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                hits += [n.lineno for n in node.args if isinstance(n, ast.Constant) and n.value == "api_key"]
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                if node.slice.value == "api_key":
                    hits.append(node.lineno)
        if hits:
            out[str(path.relative_to(ROOT))] = sorted(set(hits))
    return out


def test_the_scan_is_live():
    """POSITIVE CONTROL. An instrument that matched nothing would report a clean
    sweep forever, and the count below is the whole argument for the goal."""
    assert len(_inline_api_key_readers()) > 5, "the api_key scan found almost nothing — check the AST match"


def test_the_topic_resolver_is_no_longer_one_of_them():
    """THE fix, asserted structurally rather than by reading the diff."""
    offenders = _inline_api_key_readers()

    assert "core/cockpit/notification_channels.py" not in offenders
    assert "cortex_bearer" in (ROOT / "core" / "cockpit" / "notification_channels.py").read_text()


def test_the_remaining_scatter_is_recorded_not_forgotten():
    """Goal c22e6712 in one assertion.

    18 files resolved `api_key` inline while never calling `cortex_bearer` —
    each its own 401 contract, each blind to OAuth-only seats. Consolidating all
    of them is a separate sweep; what must not happen is the number growing
    quietly while the goal sits open. This fails if a NEW module joins the list,
    forcing the author to either use the helper or add themselves deliberately.
    """
    offenders = {f for f in _inline_api_key_readers() if f not in CREDENTIAL_OWNERS}
    known_remaining = {
        "api/serve_app.py",
        "cli/command_handlers/_workflow_postflight.py",
        "cli/command_handlers/artifact_log_commands.py",
        "cli/command_handlers/doctor.py",
        "cli/command_handlers/mesh_agreements_commands.py",
        "cli/command_handlers/mesh_commands.py",
        "cli/command_handlers/project_bootstrap.py",
        "cli/command_handlers/system_event.py",
        "core/cockpit/auto_accept.py",
        "core/loop_scheduler/listener.py",
        "core/loop_scheduler/liveness_probe.py",
        "core/loop_scheduler/systemd.py",
        "core/modules/executors.py",
        "plugins/claude-code-integration/hooks/session-end-postflight.py",
        "plugins/claude-code-integration/hooks/session-init.py",
    }

    new = offenders - known_remaining
    assert not new, (
        f"new module(s) resolving api_key inline instead of via cortex_bearer: {sorted(new)}. "
        "Use `from empirica.core.auth import cortex_bearer` — an inline api_key read cannot see "
        "an OAuth-only seat or a suppressed key. If this module genuinely OWNS the credential, "
        "add it to CREDENTIAL_OWNERS deliberately."
    )
