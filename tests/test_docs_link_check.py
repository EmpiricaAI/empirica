"""Tests for `empirica docs-link-check` (general broken-link checker for tech docs)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from empirica.cli.command_handlers.docs_link_check_commands import (
    DEFAULT_SKIP_DIRS,
    _check_one_file,
    _find_md_files,
    _is_anchor_only,
    _is_external,
    _resolve_relative,
    _scan,
    handle_docs_link_check_command,
)

# ── Classifiers ───────────────────────────────────────────────────────


def test_is_external_recognises_common_schemes():
    assert _is_external("https://example.com")
    assert _is_external("http://example.com")
    assert _is_external("mailto:foo@bar.com")
    assert _is_external("ftp://files.example.com")
    assert _is_external("tel:+1234567890")
    assert not _is_external("./local.md")
    assert not _is_external("../other/file.md")
    assert not _is_external("file.md")


def test_is_anchor_only_pure_fragment():
    assert _is_anchor_only("#section")
    assert _is_anchor_only("#")
    assert not _is_anchor_only("file.md#section")
    assert not _is_anchor_only("./file.md")


def test_resolve_relative_strips_anchor_and_query(tmp_path):
    src = tmp_path / "src.md"
    src.write_text("x", encoding="utf-8")
    target = tmp_path / "target.md"
    target.write_text("x", encoding="utf-8")

    assert _resolve_relative(src, "target.md#section") == target.resolve()
    assert _resolve_relative(src, "target.md?query=1") == target.resolve()
    assert _resolve_relative(src, "target.md") == target.resolve()


def test_resolve_relative_pure_anchor_returns_source(tmp_path):
    src = tmp_path / "src.md"
    src.write_text("x", encoding="utf-8")
    assert _resolve_relative(src, "") == src


# ── File walking ──────────────────────────────────────────────────────


def test_find_md_files_walks_recursively(tmp_path):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.md").write_text("x", encoding="utf-8")
    (tmp_path / "sub" / "deep").mkdir()
    (tmp_path / "sub" / "deep" / "c.md").write_text("x", encoding="utf-8")

    found = _find_md_files(tmp_path, frozenset())
    names = sorted(p.name for p in found)
    assert names == ["a.md", "b.md", "c.md"]


def test_find_md_files_skips_default_dirs(tmp_path):
    """Files inside .git, .venv, node_modules, _archive, etc. are skipped."""
    (tmp_path / "active.md").write_text("x", encoding="utf-8")
    for skip in [".git", ".venv", "node_modules", "_archive"]:
        d = tmp_path / skip
        d.mkdir()
        (d / "skipped.md").write_text("x", encoding="utf-8")

    found = _find_md_files(tmp_path, DEFAULT_SKIP_DIRS)
    names = [p.name for p in found]
    assert "active.md" in names
    assert "skipped.md" not in names


def test_find_md_files_skips_only_md_extension(tmp_path):
    (tmp_path / "doc.md").write_text("x", encoding="utf-8")
    (tmp_path / "code.py").write_text("x", encoding="utf-8")
    (tmp_path / "README").write_text("x", encoding="utf-8")  # no .md extension

    found = _find_md_files(tmp_path, frozenset())
    assert len(found) == 1
    assert found[0].name == "doc.md"


# ── Single-file link extraction ───────────────────────────────────────


def test_check_one_file_no_links(tmp_path):
    f = tmp_path / "no-links.md"
    f.write_text("Just plain text. No links here.", encoding="utf-8")
    assert _check_one_file(f) == []


def test_check_one_file_external_links_pass_through(tmp_path):
    f = tmp_path / "external.md"
    f.write_text(
        "See [example](https://example.com) or [docs](http://docs.example.com).",
        encoding="utf-8",
    )
    assert _check_one_file(f) == []


def test_check_one_file_anchor_only_links_pass_through(tmp_path):
    f = tmp_path / "anchors.md"
    f.write_text("Jump to [intro](#introduction) or [outro](#outro).", encoding="utf-8")
    assert _check_one_file(f) == []


def test_check_one_file_existing_relative_link_passes(tmp_path):
    target = tmp_path / "target.md"
    target.write_text("Hello", encoding="utf-8")
    f = tmp_path / "src.md"
    f.write_text("See [target](target.md)", encoding="utf-8")
    assert _check_one_file(f) == []


def test_check_one_file_broken_relative_link_caught(tmp_path):
    f = tmp_path / "src.md"
    f.write_text("See [missing](does-not-exist.md)", encoding="utf-8")

    broken = _check_one_file(f)
    assert len(broken) == 1
    assert broken[0]["target"] == "does-not-exist.md"
    assert "not found" in broken[0]["reason"]


def test_check_one_file_anchor_on_existing_file_passes(tmp_path):
    """`target.md#section` works if target.md exists (anchor not validated)."""
    target = tmp_path / "target.md"
    target.write_text("# Section\nbody", encoding="utf-8")
    f = tmp_path / "src.md"
    f.write_text("See [section](target.md#section)", encoding="utf-8")
    assert _check_one_file(f) == []


def test_check_one_file_image_links_also_checked(tmp_path):
    """`![alt](image.png)` is also walked — if image missing, broken."""
    f = tmp_path / "src.md"
    f.write_text("![diagram](missing-image.png)", encoding="utf-8")

    broken = _check_one_file(f)
    assert len(broken) == 1
    assert broken[0]["target"] == "missing-image.png"


def test_check_one_file_skips_jinja_placeholders(tmp_path):
    """`{{ var }}` in link target shouldn't trigger broken-link error."""
    f = tmp_path / "src.md"
    f.write_text("[link]({{ base }}/path.md)", encoding="utf-8")
    assert _check_one_file(f) == []


def test_check_one_file_reports_line_numbers(tmp_path):
    f = tmp_path / "src.md"
    f.write_text(
        "line 1\nline 2 [bad](missing.md)\nline 3\n",
        encoding="utf-8",
    )
    broken = _check_one_file(f)
    assert len(broken) == 1
    assert broken[0]["line"] == 2


# ── Tier-prioritised scan ──────────────────────────────────────────────


def test_scan_clean_repo_passes(tmp_path):
    (tmp_path / "README.md").write_text("# Project\n[link](sub/file.md)", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "file.md").write_text("x", encoding="utf-8")

    report = _scan(tmp_path, frozenset())
    assert report["passed"] is True
    assert report["broken_total"] == 0
    assert report["scanned_files"] == 2


def test_scan_broken_link_in_top_readme_lands_in_tier_1(tmp_path):
    (tmp_path / "README.md").write_text("[bad](missing.md)", encoding="utf-8")

    report = _scan(tmp_path, frozenset())
    assert report["broken_total"] == 1
    tier_1 = report["tiers"]["tier_1_top_readme"]
    assert tier_1["broken_total"] == 1
    assert tier_1["files"][0]["file"] == "README.md"


def test_scan_folder_readme_lands_in_tier_2(tmp_path):
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "README.md").write_text("[bad](nope.md)", encoding="utf-8")

    report = _scan(tmp_path, frozenset())
    assert report["tiers"]["tier_1_top_readme"]["broken_total"] == 0
    assert report["tiers"]["tier_2_folder_readmes"]["broken_total"] == 1


def test_scan_other_md_lands_in_tier_3(tmp_path):
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    (tmp_path / "guide.md").write_text("[bad](nope.md)", encoding="utf-8")

    report = _scan(tmp_path, frozenset())
    assert report["tiers"]["tier_1_top_readme"]["broken_total"] == 0
    assert report["tiers"]["tier_2_folder_readmes"]["broken_total"] == 0
    assert report["tiers"]["tier_3_other_md"]["broken_total"] == 1


def test_scan_aggregates_broken_count_across_tiers(tmp_path):
    (tmp_path / "README.md").write_text("[a](x.md)", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "README.md").write_text("[b](y.md)\n[c](z.md)", encoding="utf-8")
    (tmp_path / "guide.md").write_text("[d](w.md)", encoding="utf-8")

    report = _scan(tmp_path, frozenset())
    assert report["broken_total"] == 4


def test_scan_respects_skip_dirs(tmp_path):
    """Files inside skip_dirs aren't even walked."""
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    archive = tmp_path / "_archive"
    archive.mkdir()
    (archive / "old.md").write_text("[bad](missing.md)", encoding="utf-8")

    report = _scan(tmp_path, DEFAULT_SKIP_DIRS)
    assert report["broken_total"] == 0


# ── CLI handler integration ────────────────────────────────────────────


def test_handler_returns_0_on_clean(tmp_path, capsys):
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    args = SimpleNamespace(root=str(tmp_path), exclude=None, output="human")

    exit_code = handle_docs_link_check_command(args)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "0 broken" in captured.out or "All links valid" in captured.out


def test_handler_returns_1_on_broken(tmp_path):
    (tmp_path / "README.md").write_text("[bad](missing.md)", encoding="utf-8")
    args = SimpleNamespace(root=str(tmp_path), exclude=None, output="json")

    import io
    import sys as _sys
    buf = io.StringIO()
    old_stdout = _sys.stdout
    _sys.stdout = buf
    try:
        exit_code = handle_docs_link_check_command(args)
    finally:
        _sys.stdout = old_stdout

    assert exit_code == 1
    import json as _json
    report = _json.loads(buf.getvalue())
    assert report["passed"] is False
    assert report["broken_total"] == 1


def test_handler_returns_2_on_invalid_root(tmp_path, capsys):
    args = SimpleNamespace(
        root=str(tmp_path / "does-not-exist"),
        exclude=None,
        output="human",
    )
    exit_code = handle_docs_link_check_command(args)
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "is not a directory" in captured.err


def test_handler_extra_excludes_take_effect(tmp_path):
    """--exclude X should add X to the skip set."""
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    custom = tmp_path / "my_archive"
    custom.mkdir()
    (custom / "old.md").write_text("[bad](missing.md)", encoding="utf-8")

    # Without exclude — should catch the bad link in my_archive
    args_no_exclude = SimpleNamespace(root=str(tmp_path), exclude=None, output="json")
    import io
    import sys as _sys
    buf = io.StringIO()
    old = _sys.stdout
    _sys.stdout = buf
    try:
        rc1 = handle_docs_link_check_command(args_no_exclude)
    finally:
        _sys.stdout = old
    assert rc1 == 1

    # With exclude — my_archive is skipped
    args_with_exclude = SimpleNamespace(root=str(tmp_path), exclude=["my_archive"], output="json")
    buf2 = io.StringIO()
    _sys.stdout = buf2
    try:
        rc2 = handle_docs_link_check_command(args_with_exclude)
    finally:
        _sys.stdout = old
    assert rc2 == 0


def test_handler_output_format_json_returns_parseable_report(tmp_path):
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    args = SimpleNamespace(root=str(tmp_path), exclude=None, output="json")

    import io
    import json as _json
    import sys as _sys

    buf = io.StringIO()
    old = _sys.stdout
    _sys.stdout = buf
    try:
        handle_docs_link_check_command(args)
    finally:
        _sys.stdout = old

    report = _json.loads(buf.getvalue())
    # Wire shape that compliance pipeline / CI consumers depend on
    assert "scanned_files" in report
    assert "broken_total" in report
    assert "passed" in report
    assert "tiers" in report
    assert set(report["tiers"].keys()) == {
        "tier_1_top_readme", "tier_2_folder_readmes", "tier_3_other_md"
    }


def test_against_real_empirica_repo_clean():
    """Smoke: the real empirica repo (post-fix) should have 0 broken links."""
    repo_root = Path(__file__).resolve().parent.parent
    args = SimpleNamespace(root=str(repo_root), exclude=None, output="json")

    import io
    import json as _json
    import sys as _sys

    buf = io.StringIO()
    old = _sys.stdout
    _sys.stdout = buf
    try:
        rc = handle_docs_link_check_command(args)
    finally:
        _sys.stdout = old

    report = _json.loads(buf.getvalue())
    assert rc == 0, f"empirica repo has broken links: {report['broken_total']}"
    assert report["passed"] is True
