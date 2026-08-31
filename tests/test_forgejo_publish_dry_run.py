"""A verb with no non-mutating mode can be used but not investigated.

`forgejo-publish` provisions a repository. It had no preview, so the only way to
learn what it would do was to let it do it — which blocks two separate things: a
caller cannot review a provisioning run before accepting it, and nobody can
DIAGNOSE the mint path without creating a real repository on shared infrastructure
to answer the question. One absence, two blockages, and the second is invisible
until someone tries to check.

The preview stops before the cortex POST, because the POST is the mint.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from empirica.cli.command_handlers import forgejo_commands


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text("ai_id: probe-project\nproject_id: probe-project\n")
    return tmp_path


@pytest.fixture
def no_mint(monkeypatch):
    """Make the mint EXPLODE if it is reached.

    A preview test that merely asserts on output would pass just as well against a
    verb that provisioned first and printed afterwards. The only assertion that
    means anything here is that the mutating call was never made.
    """

    def _boom(*a, **k):
        raise AssertionError("the cortex POST was reached — the dry run provisioned")

    monkeypatch.setattr(forgejo_commands, "_forgejo_publish_post", _boom)
    monkeypatch.setattr(forgejo_commands, "_resolve_cortex_config", lambda: ("https://cortex.example", "k-123"))


def _run(project: Path, **kw) -> int:
    args = Namespace(
        path=str(project),
        rotate=kw.get("rotate", False),
        description=None,
        dry_run=kw.get("dry_run", True),
        output=kw.get("output", "json"),
    )
    return forgejo_commands.handle_forgejo_publish_command(args)


def test_the_dry_run_never_reaches_the_mint(project, no_mint, capsys):
    """THE property. Everything else in this file is about legibility; this is the
    one that makes it a preview rather than a differently-worded provision."""
    rc = _run(project)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True


def test_it_names_the_project_rather_than_counting(project, no_mint, capsys):
    """A count is unreviewable. The value of a preview is seeing that the thing
    about to be published is a scratch directory nobody meant to publish, and only
    the name carries that."""
    _run(project)
    w = json.loads(capsys.readouterr().out)["would_provision"]

    assert w["project_id"] == "probe-project"
    assert str(project) in w["project_path"]
    assert w["cortex_url"] == "https://cortex.example"


def test_it_does_not_invent_the_repo_url(project, no_mint, capsys):
    """The URL and refspecs are assigned by cortex at provision time. A preview that
    guessed them would be the false-success shape this verb already guards against
    elsewhere — plausible output that no one can distinguish from a real result."""
    _run(project)
    payload = json.loads(capsys.readouterr().out)

    assert payload["dry_run"] is True, "positive control: this is the preview payload"
    assert "forgejo_repo_url" not in payload
    assert "refspecs" not in payload


def test_rotate_is_surfaced_because_it_revokes(project, no_mint, capsys):
    """`--rotate` revokes the prior token. A preview that hid the destructive half of
    the flags would be worse than no preview: it would confer confidence over exactly
    the part that needs it least."""
    _run(project, rotate=True)
    assert json.loads(capsys.readouterr().out)["would_provision"]["rotate_token"] is True


def test_the_human_render_does_not_claim_already_provisioned(project, no_mint, capsys):
    """REGRESSION on a bug this change nearly introduced.

    The already-published branch keys on `note` present and `push_results` absent —
    which a preview payload also satisfies. Without an explicit dry-run branch the
    human renderer printed "already provisioned" for a run that checked nothing.
    Adding a probe mode to a renderer that reports state by INFERENCE is how the
    probe starts asserting the thing it exists to avoid asserting.
    """
    _run(project, output="human")
    out = capsys.readouterr().out

    assert "DRY RUN" in out
    assert "probe-project" in out, "positive control: the preview rendered"
    assert "already provisioned" not in out


def test_without_the_flag_the_mint_is_still_reached(project, no_mint):
    """POSITIVE CONTROL on the whole file. If the verb never reached the POST at all,
    every test above would pass while the feature was inert."""
    with pytest.raises(AssertionError, match="the cortex POST was reached"):
        _run(project, dry_run=False)
