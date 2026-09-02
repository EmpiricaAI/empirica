"""No sync verb may guess a remote. Unset means REFUSE, and the refusal must be usable.

Three seats in one week, one literal each, opposite invisible failures:

- ``code_remote`` defaulted to ``origin``; on one seat ``origin`` is a PUBLIC GitHub
  repo, so enabling auto-push "for a private backup" would have published;
- ``remote`` defaulted to ``forgejo``; on a seat without it, notes synced nowhere for
  weeks and nothing said so;
- ``sync-status`` took its remote from args with a literal default and never read the
  config, so a correctly-configured seat was told it was unconfigured.

**Guessing wrong about a remote is publishing**, and a default that is usually right
is the worst shape for that: it works until the seat where it does not, and never
announces which case you are in.

Every test here asserts on the REFUSAL as much as on the absence, because refusing
without naming the fix just moves the invisibility one step along.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from empirica.cli.command_handlers import sync_commands
from empirica.core import sync_remotes
from empirica.core.sync_remotes import CODE, NOTES

# --------------------------------------------------------------------------- resolver


def test_unset_resolves_to_none_not_a_literal():
    """THE regression, both kinds. Nothing in the resolution order ends in a string."""
    assert sync_remotes.resolve(NOTES, {}) is None
    assert sync_remotes.resolve(CODE, {}) is None


def test_no_resolution_order_ends_in_a_literal():
    """The CLASS. A future key added with a literal tail would reintroduce the defect,
    so the property is asserted on the order itself, not just on today's two kinds."""
    for kind, keys in sync_remotes.RESOLUTION_ORDER.items():
        assert all(isinstance(k, str) for k in keys)
        assert sync_remotes.resolve(kind, {}) is None, f"{kind} resolves without config"


def test_configured_values_do_resolve():
    """POSITIVE CONTROL. A resolver that returned None unconditionally would satisfy
    every assertion above while breaking sync entirely."""
    assert sync_remotes.resolve(NOTES, {"notes_remote": "fj"}) == "fj"
    assert sync_remotes.resolve(CODE, {"code_remote": "gh"}) == "gh"


def test_explicit_beats_config():
    """A `--remote` flag must not be swallowed by the config read — that would trade
    one ignored input for another."""
    assert sync_remotes.resolve(NOTES, {"notes_remote": "fj"}, explicit="other") == "other"


def test_notes_falls_through_to_the_historical_key():
    """`remote` is what sync-push has always read. Without this step,
    `sync-config notes_remote X` would retarget profile-sync and not sync-push —
    one key, two verbs, two destinations."""
    assert sync_remotes.resolve(NOTES, {"remote": "fj"}) == "fj"
    assert sync_remotes.resolve(NOTES, {"notes_remote": "a", "remote": "b"}) == "a"


def test_code_never_falls_through_to_the_notes_key():
    """The two are different disclosure decisions. A remote chosen to hold PRIVATE
    notes must never become the destination for code because only one was set."""
    assert sync_remotes.resolve(CODE, {"remote": "private-fj", "notes_remote": "private-fj"}) is None


# --------------------------------------------------------------------------- refusal


@pytest.fixture
def repo_with_remotes(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    for name in ("forgejo", "upstream"):
        subprocess.run(
            ["git", "remote", "add", name, f"https://example.invalid/{name}.git"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
    return tmp_path


def test_refusal_names_the_remotes_that_actually_exist(repo_with_remotes):
    """A refusal that says only 'not configured' sends the reader hunting for what
    they may choose. It must answer that from the repo in front of them."""
    payload = sync_remotes.refusal(NOTES, root=repo_with_remotes)

    assert payload["ok"] is False
    assert payload["available_remotes"] == ["forgejo", "upstream"]
    assert "forgejo" in payload["hint"] and "upstream" in payload["hint"]


def test_refusal_names_the_exact_command(repo_with_remotes):
    """And the command must be the one the CLI actually accepts — positional
    `key value`. A `--set key=value` form was written into two hints and does not
    exist; an advertised flag that no parser reads is worse than no hint."""
    payload = sync_remotes.refusal(CODE, root=repo_with_remotes)

    assert payload["fix"] == "empirica sync-config code_remote <remote>"
    assert "--set" not in payload["hint"]


def test_refusal_says_so_when_there_are_no_remotes_at_all(tmp_path):
    """NEGATIVE CASE that is not an error case. 'none' is the honest answer, and the
    fix is a git remote add rather than a sync-config."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    payload = sync_remotes.refusal(NOTES, root=tmp_path)

    assert payload["available_remotes"] == []
    assert "git remote add" in payload["hint"]


def test_render_refusal_carries_both_facts(repo_with_remotes):
    """The human form must not be thinner than the JSON one — a practitioner reading
    the terminal gets the same two answers a script gets."""
    rendered = sync_remotes.render_refusal(sync_remotes.refusal(NOTES, root=repo_with_remotes))

    assert "REFUSED" in rendered
    assert "forgejo" in rendered
    assert "empirica sync-config notes_remote" in rendered


# --------------------------------------------------------------------------- defaults


def test_shipped_defaults_carry_no_remote():
    """The config the CLI merges must not reintroduce what the resolver refuses."""
    for key in ("remote", "notes_remote", "code_remote"):
        assert sync_commands.DEFAULT_SYNC_CONFIG[key] is None, f"{key} has a default again"


@pytest.mark.parametrize("value", [None, "forgejo"], ids=["get-one-key", "set-one-key"])
def test_single_key_config_renders_in_human_mode(repo_with_remotes, monkeypatch, capsys, value):
    """The single-key path never computed the remote locals while the human renderer
    read them unconditionally, so `sync-config <key> [value] --output human` raised
    UnboundLocalError on every invocation.

    Invisible because the default output is json — and `--output human` is what a
    person reaches for exactly when they are unsure whether the write landed.
    """
    from types import SimpleNamespace

    (repo_with_remotes / ".empirica").mkdir()
    (repo_with_remotes / ".empirica" / "config.yaml").write_text('version: "2.0"\nsync: {}\n')
    monkeypatch.chdir(repo_with_remotes)
    monkeypatch.setattr(sync_commands, "_get_workspace_root", lambda: str(repo_with_remotes))

    args = SimpleNamespace(key="notes_remote", value=value, output="human", verbose=False)
    assert sync_commands.handle_sync_config_command(args) == 0
    assert "notes_remote" in capsys.readouterr().out


def test_config_key_list_is_derived_not_restated():
    """`sync-config` printed four keys while validating seven, so `code_remote`,
    `notes_remote` and `auto_push_on` were settable and undocumented at the point of
    use. A key list is exactly the thing that must be derived."""
    assert set(sync_commands.VALID_CONFIG_KEYS) == set(sync_commands.DEFAULT_SYNC_CONFIG)
    assert "code_remote" in sync_commands.VALID_CONFIG_KEYS
    assert "auto_push_on" in sync_commands.VALID_CONFIG_KEYS
