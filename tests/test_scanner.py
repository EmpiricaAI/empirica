"""Phase 1 tests for the AI service scanner.

Covers:
- Read-surface defaults and parse-from-yaml
- Read-surface enforcement (collectors only emit fields the surface allows)
- Scanner self-detection (the running process appears with the self flag)
- Snapshot orchestration (errors are captured, not raised)

See docs/architecture/PROPOSAL_AI_SERVICE_SCANNER.md.
"""

from __future__ import annotations

import os

import pytest

from empirica.core.scanner import (
    DEFAULT_READ_SURFACE,
    Snapshot,
    collect_snapshot,
    load_read_surface,
)
from empirica.core.scanner.env_names import collect_env_var_names
from empirica.core.scanner.processes import collect_processes
from empirica.core.scanner.read_surface import (
    PROCESS_FIELDS,
    ReadSurface,
    parse_read_surface,
)


@pytest.fixture
def real_process_table():
    """Request this to scan the machine's real process table instead of the small one below."""


@pytest.fixture(autouse=True)
def _small_process_table(request, monkeypatch):
    """Scan a real but small process table: this process and its ancestors.

    The collector builds a heavy row per process (memory_maps alone is ~4s of a ~7s scan over the 577 processes on a dev box), and 16 of these
    tests each paid for a full scan while asserting on the scanner's behaviour, not on the machine. ~106s of a 823s suite. The processes are
    real `psutil.Process` objects, so every code path runs; one test opts back into the real table (`real_process_table`)."""
    if "real_process_table" in request.fixturenames:
        return
    import psutil

    me = psutil.Process(os.getpid())
    keep = [me, *me.parents()][:4]
    monkeypatch.setattr(psutil, "process_iter", lambda *_a, **_k: iter(keep))


# ── Read-surface defaults + parse ────────────────────────────────────────


class TestReadSurface:
    def test_default_surface_has_all_collector_fields(self):
        for name in ("process", "network", "filesystem", "process_env", "scheduled", "mcp"):
            assert getattr(DEFAULT_READ_SURFACE, name)

    def test_default_process_surface_includes_self_flag(self):
        assert "is_scanner_self" in DEFAULT_READ_SURFACE.process

    def test_parse_drops_unknown_fields(self):
        cfg = {"process": ["pid", "cmdline", "banana_count"]}
        surface = parse_read_surface(cfg)
        assert "pid" in surface.process
        assert "cmdline" in surface.process
        assert "banana_count" not in surface.process

    def test_parse_falls_back_to_default_when_missing(self):
        cfg = {"process": ["pid"]}
        surface = parse_read_surface(cfg)
        # 'process' was overridden; 'network' falls back to default.
        assert surface.process == frozenset({"pid"})
        assert surface.network == DEFAULT_READ_SURFACE.network

    def test_load_read_surface_returns_default_when_no_yaml(self, tmp_path):
        # Path that doesn't exist → default
        result = load_read_surface(tmp_path / "missing.yaml")
        assert result == DEFAULT_READ_SURFACE

    def test_load_read_surface_parses_yaml(self, tmp_path):
        import yaml

        yaml_path = tmp_path / "project.yaml"
        yaml_path.write_text(
            yaml.safe_dump(
                {
                    "cockpit": {
                        "scanner": {
                            "read_surface": {
                                "process": ["pid", "cmdline"],
                                "network": ["pid", "peer_host"],
                            },
                            "relevant_globs_for_coverage": {
                                "code": ["empirica/**/*.py"],
                            },
                        }
                    }
                }
            )
        )

        surface = load_read_surface(yaml_path)
        assert surface.process == frozenset({"pid", "cmdline"})
        assert surface.network == frozenset({"pid", "peer_host"})
        assert surface.relevant_globs_for_coverage == {"code": ["empirica/**/*.py"]}

    def test_filter_dict_drops_disallowed_keys(self):
        surface = ReadSurface(
            process=frozenset({"pid", "cmdline"}),
            network=frozenset(),
            filesystem=frozenset(),
            process_env=frozenset(),
            scheduled=frozenset(),
            mcp=frozenset(),
        )
        row = {"pid": 1, "cmdline": "init", "memory_mb": 99, "username": "root"}
        filtered = surface.filter_dict("process", row)
        assert filtered == {"pid": 1, "cmdline": "init"}


# ── Process collector + self-detection ────────────────────────────────────


class TestProcessCollector:
    def test_emits_only_surface_fields(self):
        surface = ReadSurface(
            process=frozenset({"pid", "cmdline", "is_scanner_self"}),
            network=frozenset(),
            filesystem=frozenset(),
            process_env=frozenset(),
            scheduled=frozenset(),
            mcp=frozenset(),
        )
        rows, _coverage = collect_processes(surface)
        # If psutil is unavailable the collector returns ([], coverage); either
        # way the invariant must hold for whatever rows do come back.
        for row in rows:
            assert set(row.keys()) <= surface.process

    def test_the_real_process_table_scans_and_includes_the_scanner(self, real_process_table):
        """The one full scan: every process on the box, the way `empirica scan` sees it."""
        rows, coverage = collect_processes(DEFAULT_READ_SURFACE)
        assert rows and coverage["succeeded"] == len(rows) and coverage["attempted"] >= len(rows)
        assert [r["pid"] for r in rows if r.get("is_scanner_self")] == [os.getpid()]

    def test_the_small_table_is_this_process_and_its_ancestors(self):
        rows, coverage = collect_processes(DEFAULT_READ_SURFACE)
        assert 1 <= len(rows) <= 4 and coverage["attempted"] == len(rows) + coverage["skipped"]
        assert os.getpid() in [r["pid"] for r in rows]

    def test_scanner_self_row_present(self):
        rows, _coverage = collect_processes(DEFAULT_READ_SURFACE)
        if not rows:
            # psutil unavailable in this env — degrade gracefully
            return
        self_rows = [r for r in rows if r.get("is_scanner_self")]
        assert len(self_rows) == 1
        assert self_rows[0]["pid"] == os.getpid()

    def test_coverage_meta_shape(self):
        rows, coverage = collect_processes(DEFAULT_READ_SURFACE)
        # When psutil is present, attempted should be > 0 and ratio should be set
        if rows:
            assert coverage["attempted"] > 0
            assert "succeeded" in coverage
            assert "ratio" in coverage
            assert 0.0 <= coverage["ratio"] <= 1.0

    def test_universe_includes_documented_fields(self):
        # The universe should permit every field the proposal lists
        for required in ("pid", "cmdline", "parent_pid", "age_seconds", "working_dir", "is_scanner_self"):
            assert required in PROCESS_FIELDS


# ── Env-name collector — values never leak ────────────────────────────────


class TestEnvNameCollector:
    def test_only_returns_names(self):
        fake_env = {
            "OPENAI_API_KEY": "sk-this-must-never-be-emitted",
            "PATH": "/usr/bin",
            "ANTHROPIC_API_KEY": "sk-ant-secret",
            "HOME": "/home/test",
        }
        payload, coverage = collect_env_var_names(DEFAULT_READ_SURFACE, env=fake_env)
        names = payload["var_names_only"]
        assert "OPENAI_API_KEY" in names
        assert "ANTHROPIC_API_KEY" in names
        assert "PATH" not in names  # not interesting
        # The value must never appear anywhere in the result
        joined = repr((payload, coverage))
        assert "sk-this-must-never-be-emitted" not in joined
        assert "sk-ant-secret" not in joined
        # Coverage shape
        assert coverage["total_env_vars"] == 4
        assert coverage["interesting_matches"] == 2

    def test_empty_when_surface_disallows(self):
        surface = ReadSurface(
            process=frozenset(),
            network=frozenset(),
            filesystem=frozenset(),
            process_env=frozenset(),
            scheduled=frozenset(),
            mcp=frozenset(),
        )
        payload, _coverage = collect_env_var_names(surface, env={"OPENAI_API_KEY": "x"})
        assert payload == {"var_names_only": []}


# ── Snapshot orchestrator ─────────────────────────────────────────────────


class TestSnapshotOrchestrator:
    def test_produces_snapshot(self):
        snap = collect_snapshot(DEFAULT_READ_SURFACE)
        assert isinstance(snap, Snapshot)
        assert snap.scan_id
        assert snap.started_at > 0
        assert snap.finished_at and snap.finished_at >= snap.started_at
        assert snap.scanner_pid == os.getpid()

    def test_snapshot_serializes_to_json(self):
        snap = collect_snapshot(DEFAULT_READ_SURFACE)
        text = snap.to_json()
        assert text.startswith("{")
        # round-trip
        import json as _json

        data = _json.loads(text)
        assert data["scan_id"] == snap.scan_id

    def test_collector_errors_are_captured_not_raised(self, monkeypatch):
        # Force one collector to raise; the snapshot should still complete
        from empirica.core.scanner import snapshot as snapshot_module

        def boom(_surface):
            raise RuntimeError("synthetic")

        monkeypatch.setattr(snapshot_module, "collect_processes", boom)
        snap = collect_snapshot(DEFAULT_READ_SURFACE)
        assert any("processes" in err for err in snap.errors)
        assert snap.snapshot["processes"] == []

    def test_snapshot_has_coverage_block(self):
        snap = collect_snapshot(DEFAULT_READ_SURFACE)
        cov = snap.snapshot.get("coverage")
        assert isinstance(cov, dict)
        for key in ("processes", "network", "scheduled", "process_env", "filesystem", "relevant_globs"):
            assert key in cov

    def test_relevant_globs_coverage_counts_matches(self, tmp_path):
        # Plant a known set of files
        (tmp_path / "a.py").write_text("# a")
        (tmp_path / "b.py").write_text("# b")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "one.md").write_text("# one")

        surface = ReadSurface(
            process=frozenset(),
            network=frozenset(),
            filesystem=frozenset(),
            process_env=frozenset(),
            scheduled=frozenset(),
            mcp=frozenset(),
            relevant_globs_for_coverage={
                "code": ["*.py"],
                "docs": ["docs/*.md"],
            },
        )
        snap = collect_snapshot(surface, project_root=tmp_path)
        globs_cov = snap.snapshot["coverage"]["relevant_globs"]
        assert globs_cov["code"]["total_matches"] == 2
        assert globs_cov["docs"]["total_matches"] == 1


# ── Markdown report renderer ─────────────────────────────────────────────


class TestMarkdownReport:
    def test_renders_coverage_section_first(self):
        from empirica.core.scanner.report import render_markdown

        snap = collect_snapshot(DEFAULT_READ_SURFACE)
        md = render_markdown(snap)
        # Coverage must appear before any per-collector section
        assert "## Coverage" in md
        cov_idx = md.index("## Coverage")
        for section in ("## Processes", "## Network", "## Scheduled tasks"):
            if section in md:
                assert md.index(section) > cov_idx

    def test_scanner_self_visible_in_markdown(self):
        from empirica.core.scanner.report import render_markdown

        snap = collect_snapshot(DEFAULT_READ_SURFACE)
        md = render_markdown(snap)
        if not snap.processes:
            return  # psutil missing
        # The 🔍 marker for self-row must appear in the table
        assert "🔍" in md or "is_scanner_self" in md

    def test_phase1_footer(self):
        from empirica.core.scanner.report import render_markdown

        snap = collect_snapshot(DEFAULT_READ_SURFACE)
        md = render_markdown(snap)
        assert "Phase 1 deterministic snapshot" in md


# ── CLI handler end-to-end ───────────────────────────────────────────────


class TestScanCli:
    def test_handler_returns_zero_and_emits_json(self, capsys):
        from empirica.cli.command_handlers.scan_commands import handle_scan_command

        class _Args:
            output = "json"
            save = False
            project_id = None

        rc = handle_scan_command(_Args())
        assert rc == 0

        captured = capsys.readouterr()
        import json as _json

        envelope = _json.loads(captured.out)
        assert envelope["ok"] is True
        snap = envelope["snapshot"]
        assert snap["scan_id"]
        # Coverage block lives inside snapshot.snapshot.coverage
        assert "coverage" in snap["snapshot"]

    def test_handler_emits_markdown(self, capsys):
        from empirica.cli.command_handlers.scan_commands import handle_scan_command

        class _Args:
            output = "markdown"
            save = False
            project_id = None

        rc = handle_scan_command(_Args())
        captured = capsys.readouterr()
        assert rc == 0
        assert captured.out.startswith("# empirica scan")
        assert "## Coverage" in captured.out

    def test_save_writes_json_files(self, capsys, tmp_path, monkeypatch):
        from empirica.cli.command_handlers import scan_commands

        # Redirect ~/.empirica to tmp_path
        monkeypatch.setattr(
            scan_commands,
            "_empirica_home",
            lambda: tmp_path,
        )

        class _Args:
            output = "json"
            save = True
            explain = False
            project_id = "test-project"

        rc = scan_commands.handle_scan_command(_Args())
        assert rc == 0

        captured = capsys.readouterr()
        import json as _json

        envelope = _json.loads(captured.out)
        scan_path = envelope["saved"]["scan_path"]
        assert scan_path.startswith(str(tmp_path))
        assert (tmp_path / "last_scan_test-project.json").exists()
        assert (tmp_path / "scan_history_test-project.jsonl").exists()


class TestScanExplain:
    """Phase 2 T1 — `empirica scan --explain` hand-off to services-auditor."""

    def test_explain_markdown_emits_handoff(self, capsys, tmp_path, monkeypatch):
        from empirica.cli.command_handlers import scan_commands

        monkeypatch.setattr(scan_commands, "_empirica_home", lambda: tmp_path)

        class _Args:
            output = "markdown"
            save = False  # --explain implies --save internally
            explain = True
            project_id = "test-project"

        rc = scan_commands.handle_scan_command(_Args())
        captured = capsys.readouterr()
        assert rc == 0
        # Hand-off references the auditor skill explicitly
        assert "services-auditor" in captured.out
        assert "snapshot ready" in captured.out.lower()
        # Snapshot was force-saved
        assert (tmp_path / "last_scan_test-project.json").exists()

    def test_explain_json_envelope(self, capsys, tmp_path, monkeypatch):
        from empirica.cli.command_handlers import scan_commands

        monkeypatch.setattr(scan_commands, "_empirica_home", lambda: tmp_path)

        class _Args:
            output = "json"
            save = False
            explain = True
            project_id = "test-project"

        rc = scan_commands.handle_scan_command(_Args())
        captured = capsys.readouterr()
        assert rc == 0

        import json as _json

        env = _json.loads(captured.out)
        assert env["ok"] is True
        assert env["mode"] == "explain"
        assert env["next_step"]["skill"] == "services-auditor"
        assert env["next_step"]["snapshot_path"]
        # The full snapshot is on disk, not in the envelope (kept lean)
        assert "snapshot" not in env  # only summary keys

    def test_explain_implies_save(self, capsys, tmp_path, monkeypatch):
        """--explain auto-forces --save so the auditor has a file to read."""
        from empirica.cli.command_handlers import scan_commands

        monkeypatch.setattr(scan_commands, "_empirica_home", lambda: tmp_path)

        class _Args:
            output = "json"
            save = False  # caller did not request save
            explain = True
            project_id = "test-project"

        scan_commands.handle_scan_command(_Args())
        # Scan was persisted regardless
        assert (tmp_path / "last_scan_test-project.json").exists()


class TestServicesAuditorSkill:
    """The skill must be discoverable by the plugin loader."""

    def test_skill_file_exists_with_frontmatter(self):
        from pathlib import Path

        skill = Path("empirica/plugins/claude-code-integration/skills/services-auditor/SKILL.md")
        assert skill.exists(), f"missing: {skill}"
        text = skill.read_text(encoding="utf-8")
        # YAML frontmatter contract
        assert text.startswith("---\n")
        assert "name: services-auditor" in text
        # The two load-bearing things every reader must see in the skill
        assert "two-tier" in text.lower() or "tier 1" in text.lower()
        assert "citation" in text.lower()
