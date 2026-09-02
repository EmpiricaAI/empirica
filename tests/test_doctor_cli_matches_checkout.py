"""The version number cannot tell you whether the CLI is running your code.

A pipx copy and a working tree both report `1.13.34` while the code differs by any
number of unreleased commits. So `--version` matching is not evidence, and never was.

**The cost is not a stale binary — it is a misattributed test result.** A peer ran a
shipped fix against a copy predating it, got pre-fix behaviour, and was one message
from reporting the fix broken. Absent this check that report is indistinguishable from
a real regression, and it is the fix that gets re-opened.

The trap that hides it: an interpreter invoked from inside a checkout puts the cwd on
`sys.path`, so `import empirica` resolves to the checkout while the console script
loads the installed copy. Two practitioners hit exactly that on the same day — the
second an hour after reading the first's warning about it — which is why the check
asks the CLI rather than importing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from empirica.cli.command_handlers import doctor

PYPROJECT = '[project]\nname = "empirica"\nversion = "0.0.0"\n'


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch) -> Path:
    """No test here may read the developer's real `~/.empirica/registry.yaml`.

    The check falls back to that registry to find a checkout cwd-independently, so
    without this the negative cases pass or fail according to whether the box running
    them happens to have empirica checked out — green on a laptop, red in CI, and
    measuring the box either way.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A directory that LOOKS like an empirica checkout by content, not by name."""
    (tmp_path / "empirica").mkdir()
    (tmp_path / "empirica" / "__init__.py").write_text("")
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    return tmp_path


def _cli_reports(monkeypatch, package_dir: Path | str | None):
    """Stub what `empirica --version` says its Install path is."""
    monkeypatch.setattr(
        doctor,
        "_cli_package_dir",
        lambda: Path(package_dir) if package_dir is not None else None,
    )


def test_a_copy_while_standing_in_a_checkout_warns(checkout, monkeypatch, tmp_path):
    """THE regression. Both sides report the same version; only the paths differ."""
    _cli_reports(monkeypatch, tmp_path / "pipx" / "venvs" / "empirica" / "lib" / "site-packages")

    check = doctor.check_cli_matches_checkout(cwd=checkout)

    assert check.status == doctor.WARN
    assert "NOT the checkout" in check.detail
    assert "same version number" in check.detail, "the reader must be told the version cannot decide this"
    assert "--editable" in check.hint, "and given the command that fixes it"


def test_an_editable_install_passes(checkout, monkeypatch):
    """POSITIVE CONTROL. A check that warned unconditionally would satisfy the test
    above while making `doctor` cry wolf on every correctly-installed box — and a
    check that cries wolf gets silenced the first time it does."""
    _cli_reports(monkeypatch, checkout)

    check = doctor.check_cli_matches_checkout(cwd=checkout)

    assert check.status == doctor.PASS
    assert "editable" in check.detail


def test_no_checkout_anywhere_is_SKIP_not_PASS(tmp_path, monkeypatch):
    """NEGATIVE CONTROL, and a correction. Ordinary users are not in a checkout and a
    released copy is correct for them — but the comparison was not PERFORMED, and
    folding *not checked* into *passed* is how an exemption reports clean forever.
    doctor already uses SKIP elsewhere in the same output."""
    _cli_reports(monkeypatch, tmp_path / "site-packages")

    check = doctor.check_cli_matches_checkout(cwd=tmp_path)

    assert check.status == doctor.SKIP
    assert "nothing to compare" in check.detail


def test_the_skew_is_found_from_outside_the_checkout(tmp_path, monkeypatch, checkout, isolated_home):
    """THE second regression, reported by a peer against the first version of this
    check. The skew is a property of the INSTALL, not of the cwd — but the original
    only walked up from cwd, so a developer running `doctor` from any other repo was
    told PASS while the skew was live on the same box. The practitioners who most need
    this are exactly the ones not standing in core's tree.

    Here the checkout is registered with the daemon and the cwd is somewhere else.
    """
    elsewhere = tmp_path / "some" / "other" / "repo"
    elsewhere.mkdir(parents=True)
    registry = isolated_home / ".empirica"
    registry.mkdir(parents=True)
    (registry / "registry.yaml").write_text(f"version: 1\nprojects:\n- path: {checkout}\n")
    _cli_reports(monkeypatch, tmp_path / "pipx-site-packages")

    check = doctor.check_cli_matches_checkout(cwd=elsewhere)

    assert check.status == doctor.WARN
    assert str(checkout) in check.detail


def test_a_stale_registry_entry_does_not_break_the_check(tmp_path, monkeypatch, isolated_home):
    """Registry paths go stale — a deleted or moved project must be skipped, not
    treated as a checkout and not raised on."""
    registry = isolated_home / ".empirica"
    registry.mkdir(parents=True)
    (registry / "registry.yaml").write_text(f"version: 1\nprojects:\n- path: {tmp_path / 'gone'}\n- path: null\n")
    _cli_reports(monkeypatch, tmp_path / "site-packages")

    assert doctor.check_cli_matches_checkout(cwd=tmp_path).status == doctor.SKIP


def test_an_unresolvable_cli_warns_rather_than_claiming_a_match(checkout, monkeypatch):
    """Introspection that fails must not read as agreement. Silence here would be the
    same defect the check exists to remove."""
    _cli_reports(monkeypatch, None)

    check = doctor.check_cli_matches_checkout(cwd=checkout)

    assert check.status == doctor.WARN


def test_a_lookalike_directory_is_not_a_checkout(tmp_path, monkeypatch):
    """Identified by content, not by name. A clone, a backup, or a docs folder called
    `empirica/` must not make every user look like a developer."""
    (tmp_path / "empirica").mkdir()
    (tmp_path / "empirica" / "__init__.py").write_text("")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "something-else"\n')
    _cli_reports(monkeypatch, tmp_path / "site-packages")

    # SKIP, not PASS: the lookalike is correctly NOT treated as a checkout, so there
    # is nothing to compare — which is the SKIP verdict, not agreement.
    assert doctor.check_cli_matches_checkout(cwd=tmp_path).status == doctor.SKIP


def test_the_check_is_registered(tmp_path):
    """A check nothing runs is a function. Asserts it appears in the suite `doctor`
    actually executes, since that is the surface anyone reads."""
    names = {c.name for c in doctor.run_all_checks(cwd=tmp_path)}

    assert "CLI matches checkout" in names
