"""Two surfaces that answered the reader's question wrongly, in opposite directions.

One had the answer and did not print it; the other printed an answer it did not have.

`projects-sync` collects `{name, outcome, status, reason}` per project and the human
renderer printed only the counts — so "19 registered, 3 failed" named nothing to act
on, while the detail sat in the same dict one key away. `--output json` carried it
the whole time, which means the people most likely to hit this are the ones least
likely to be piping to jq.

`sync-status` rendered `Code: <remote> (public)` beside the notes line as though both
were synced. **Nothing in empirica pushes code** — sync-push's only refspecs are the
two notes refs. A label with no behaviour behind it is worse than an absent feature,
because it answers the exact question the reader came to ask and stops them looking.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "empirica/cli/command_handlers/projects_commands.py"
SYNC = ROOT / "empirica/cli/command_handlers/sync_commands.py"


def _render_cortex_block(capsys, cortex: dict) -> str:
    """Drive the REAL renderer.

    Re-implementing the loop here would assert that a copy of the logic behaves, which
    is the failure this whole module is about — a surface that agrees with the test and
    disagrees with what ships.
    """
    from empirica.cli.command_handlers.projects_commands import _emit_sync_summary

    _emit_sync_summary(
        {
            "discovered": 1,
            "manifest_written": False,
            "registry": None,
            "cortex": cortex,
            "phases_skipped": [],
        },
        "human",
        dry_run=False,
    )
    return capsys.readouterr().err


def test_failed_projects_are_named_with_status_and_reason(capsys):
    """The report that prompted this: a fifth of the operation failed and the operator
    was given a number."""
    out = _render_cortex_block(
        capsys,
        {
            "registered": 19,
            "failed": 3,
            "cortex_url": "https://cortex.example",
            "results": [
                {"name": "ok-one", "outcome": "registered", "status": 200},
                {"name": "bad-one", "outcome": "failed", "status": 409, "reason": "slug already registered"},
                {"name": "bad-two", "outcome": "failed", "status": 500, "reason": "upstream timeout"},
            ],
        },
    )

    assert "bad-one" in out and "bad-two" in out
    assert "409" in out and "slug already registered" in out
    assert "ok-one" not in out, "successes must not be listed — the point is a short actionable tail"


def test_a_failure_with_no_reason_still_names_the_project(capsys):
    """`reason` is not guaranteed. Falling back to nothing would reproduce the defect
    for exactly the failures that carry least information."""
    out = _render_cortex_block(
        capsys,
        {
            "registered": 0,
            "failed": 1,
            "cortex_url": "https://cortex.example",
            "results": [{"name": "mystery", "outcome": "failed"}],
        },
    )
    assert "mystery" in out
    assert "no reason reported" in out


def test_an_all_clean_run_prints_no_failure_lines(capsys):
    """NEGATIVE CONTROL. A renderer that printed a line per result would look correct
    on the failure test and add noise to every successful sync."""
    out = _render_cortex_block(
        capsys,
        {
            "registered": 2,
            "failed": 0,
            "cortex_url": "https://cortex.example",
            "results": [{"name": "a", "outcome": "registered"}],
        },
    )
    assert "✗" not in out
    assert "2 registered" in out, "positive control: the renderer ran and produced its summary line"


def test_sync_status_label_tracks_whether_code_is_actually_pushed():
    """This test caught its own subject changing, which is what it was for.

    Its first form asserted the literal string "empirica never pushes code" — true
    when written and false the moment auto-push shipped. **A test that pins the
    surface WORDING breaks on a truthful change and stays green on an untruthful
    one**, which is backwards. So it now asserts the contract: the code line is
    rendered from `auto_push_on`, and both states are distinguishable.
    """
    src = SYNC.read_text()

    assert 'sync_config.get("auto_push_on")' in src, "the code label must be derived from config, not hardcoded"
    assert "auto-pushed on postflight" in src, "the ON state must say pushing happens"
    assert "auto-push is OFF" in src, "the OFF state must say it does not"
    assert "sync-push" in src, "positive control: the notes line still names the verb that delivers notes"


def test_sync_push_still_carries_only_notes_refspecs():
    """`sync-push` remains notes-only; code goes through auto_push, a separate path
    with its own dirty-tree refusal and verified-push check. If a branch refspec ever
    appears here, the two paths have merged and the whole safety argument needs
    revisiting rather than silently inheriting sync-push's semantics."""
    src = SYNC.read_text()
    assert "refs/notes/empirica" in src, "positive control: the notes refspecs are still here"
    assert "refs/heads/" not in src, "sync-push now pushes branches — revisit the auto-push safety split"


def test_the_renderer_reads_results_not_just_counts():
    """Guards the specific regression: someone tidying the block back to a single
    count line."""
    src = PROJECTS.read_text()
    assert 'c.get("results")' in src
