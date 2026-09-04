"""`projects-sync --publish`: the fleet half of forgejo provisioning.

`forgejo-publish` already provisions a managed remote and pushes ONE project.
`projects-sync` walked, registered and POSTed to Cortex and had **zero** forgejo
references — so the per-project verb existed while the fleet ask ("practitioners
get their projects onto forgejo via projects-sync") did not.

Phase 4 is a LOOP over the existing verb, not new provisioning code: a second
implementation of how a repo gets minted would drift in the direction where a
fix lands in only one.

OPT-IN, because creating repositories on shared infrastructure is irreversible
from the CLI's side and must never be a side effect of a routine sync.
"""

from __future__ import annotations

import pytest

import empirica.cli.command_handlers.projects_commands as pc


class _Args:
    # Annotated so a test can narrow `includes` to a real filter without pyright
    # inferring the attribute's type as None from the default alone.
    output: str = "json"
    roots: list[str] | None = None
    max_depth: int = 3
    include_hidden: bool = False
    dry_run: bool = False
    no_write: bool = True  # keep every test on the preview path unless it says otherwise
    no_cortex: bool = True
    publish: bool = False
    includes: list[str] | None = None
    excludes: list[str] | None = None
    prune: bool = False


@pytest.fixture
def registry(monkeypatch):
    """Two registered projects, so a loop can be told from a single call."""
    projects = [
        {"name": "alpha", "path": "/tmp/alpha"},
        {"name": "beta", "path": "/tmp/beta"},
    ]
    monkeypatch.setattr(pc, "_load_projects_for_register", lambda *a, **k: list(projects))
    monkeypatch.setattr(pc, "discover_projects", lambda **k: {"projects": projects})
    return projects


@pytest.fixture
def publish_spy(monkeypatch):
    calls = []

    def _fake(args):
        calls.append({"path": args.path, "dry_run": args.dry_run})
        return 0

    monkeypatch.setattr("empirica.cli.command_handlers.forgejo_commands.handle_forgejo_publish_command", _fake)
    return calls


def test_publish_is_OFF_by_default(registry, publish_spy, capsys):
    """THE safety property. Provisioning repos on shared infra must never be a
    side effect of a sync somebody ran to refresh a registry."""
    pc.handle_projects_sync_command(_Args())

    assert publish_spy == []
    assert "forgejo_publish" in capsys.readouterr().out


def test_publish_loops_every_registered_project(registry, publish_spy):
    args = _Args()
    args.publish = True

    result = pc._sync_phase4_forgejo_publish(args)

    assert [c["path"] for c in publish_spy] == ["/tmp/alpha", "/tmp/beta"]
    assert result["published"] == 2
    assert result["failed"] == 0


def test_dry_run_still_PREVIEWS_rather_than_skipping(registry, publish_spy, capsys):
    """The near-miss this test exists for: `--dry-run` returns early from the
    whole pipeline, so phase 4 placed after it would make the flag's own help
    text an advertised no-op — documented, accepted, silently discarded.
    The preview must reach forgejo-publish with dry_run=True."""
    args = _Args()
    args.publish = True
    args.dry_run = True

    pc.handle_projects_sync_command(args)

    assert [c["dry_run"] for c in publish_spy] == [True, True]
    assert "publish" in capsys.readouterr().out


def test_a_failing_project_does_not_abandon_the_rest(registry, monkeypatch):
    """One bad repo must not strand the fleet, and must not vanish either."""
    seen = []

    def _flaky(args):
        seen.append(args.path)
        if args.path == "/tmp/alpha":
            raise RuntimeError("forgejo refused")
        return 0

    monkeypatch.setattr("empirica.cli.command_handlers.forgejo_commands.handle_forgejo_publish_command", _flaky)
    args = _Args()
    args.publish = True

    result = pc._sync_phase4_forgejo_publish(args)

    assert seen == ["/tmp/alpha", "/tmp/beta"]
    assert result["published"] == 1
    assert result["failed"] == 1
    assert any("forgejo refused" in str(r.get("reason", "")) for r in result["results"])


def test_a_publish_failure_makes_the_whole_sync_exit_nonzero(registry, monkeypatch):
    """AND-of-all, the rule the registry phase already follows. A partial
    failure inside a rollup that reports OK is a clean status over broken work."""
    monkeypatch.setattr(
        "empirica.cli.command_handlers.forgejo_commands.handle_forgejo_publish_command",
        lambda args: 1,
    )
    args = _Args()
    args.publish = True
    args.no_write = False
    args.no_cortex = True
    monkeypatch.setattr(pc, "write_manifest", lambda *a, **k: None)
    monkeypatch.setattr(pc, "_register_discovered_to_registry", lambda *a, **k: {"added": 0})

    assert pc.handle_projects_sync_command(args) == 1


def test_a_registry_row_with_no_path_is_reported_not_skipped(monkeypatch, publish_spy):
    """Skipping it silently would make the published count disagree with the
    registry, and nothing downstream could see which row was dropped."""
    monkeypatch.setattr(pc, "_load_projects_for_register", lambda *a, **k: [{"name": "ghost"}])
    args = _Args()
    args.publish = True

    result = pc._sync_phase4_forgejo_publish(args)

    assert publish_spy == []
    assert result["failed"] == 1
    assert "no path" in result["results"][0]["reason"]


def test_filters_apply_to_publish_too(registry, publish_spy):
    """A practitioner who filtered the sync did not ask to publish the rest."""
    args = _Args()
    args.publish = True
    args.includes = ["alpha"]

    pc._sync_phase4_forgejo_publish(args)

    assert [c["path"] for c in publish_spy] == ["/tmp/alpha"]


def test_phase4_does_not_reimplement_provisioning():
    """Structural: the loop must delegate. A second minting implementation here
    would drift from forgejo-publish in the direction where a fix lands in one."""
    import inspect

    src = inspect.getsource(pc._sync_phase4_forgejo_publish)

    assert "handle_forgejo_publish_command" in src
    for reimplementation in ("urllib", "requests.post", "git init", "create_repo"):
        assert reimplementation not in src, f"phase 4 appears to provision directly via {reimplementation}"
