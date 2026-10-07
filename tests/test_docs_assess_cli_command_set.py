"""docs-assess learns a project's CLI command set from its own argparse parser, with a source scan as the fallback.

Found by the 2026-10-06 pipeline sweep (U1, the one high finding): the source scan matched single-quoted COMMAND_HANDLERS keys only,
cli_core.py uses double quotes, so the command set was {'help'} and every coverage and orphan-reference figure built on it was wrong
with no error anywhere. David's ruling: import and introspect the parser, regex only as the fallback.
Projects are built under tmp_path; nothing reads this repository.
"""

from __future__ import annotations

import textwrap

import pytest

from empirica.cli.command_handlers.docs_commands import EpistemicDocsAgent

PARSER_CLI = """
import argparse

COMMAND_HANDLERS = {
    "alpha-run": print,
    "beta-stop": print,
    "pre": print,
}

def create_argument_parser():
    parser = argparse.ArgumentParser(prog="demo")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("alpha-run")
    sub.add_parser("beta-stop")
    sub.add_parser("preflight", aliases=["pre"])
    sub.add_parser("gamma-registered-elsewhere")
    return parser
"""


def _project(tmp_path, monkeypatch, cli_source, name="demopkg"):
    root = tmp_path / "proj"
    (root / name).mkdir(parents=True)
    (root / name / "__init__.py").write_text("")
    (root / name / "cli.py").write_text(textwrap.dedent(cli_source))
    (root / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n[project.scripts]\ndemo = "{name}.cli:main"\n')
    monkeypatch.syspath_prepend(str(root))
    import importlib
    import sys

    for mod in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
        del sys.modules[mod]
    importlib.invalidate_caches()
    return EpistemicDocsAgent(root)


def test_the_command_set_comes_from_the_parser_including_aliases_and_commands_the_source_scan_cannot_see(
    tmp_path, monkeypatch
):
    agent = _project(tmp_path, monkeypatch, PARSER_CLI)
    assert agent.config.cli_framework == "argparse"
    assert agent._extract_cli_commands() == ["alpha-run", "beta-stop", "gamma-registered-elsewhere", "pre", "preflight"]


def test_double_quoted_handler_keys_are_found_by_the_fallback_scan():
    source = (
        'COMMAND_HANDLERS = {\n    "session-create": f,\n    \'single-quoted\': g,\n}\nOTHER = {"not-a-command": 1}\n'
    )
    assert EpistemicDocsAgent._scrape_argparse_commands(source) == ["session-create", "single-quoted"]


def test_the_scan_falls_back_to_the_whole_file_without_a_handlers_dict_and_counts_add_parser():
    source = 'sub.add_parser("one-cmd")\nsub.add_parser(\'two-cmd\')\n{"mapping-key": x}\n'
    assert EpistemicDocsAgent._scrape_argparse_commands(source) == ["mapping-key", "one-cmd", "two-cmd"]


def test_a_project_whose_cli_cannot_be_imported_falls_back_to_the_scan(tmp_path, monkeypatch):
    broken = 'import module_that_does_not_exist_anywhere\nCOMMAND_HANDLERS = {"scan-me": print}\n'
    agent = _project(tmp_path, monkeypatch, broken, name="brokenpkg")
    assert agent._extract_cli_commands() == ["scan-me"]


def test_a_parser_factory_that_raises_falls_back_to_the_scan(tmp_path, monkeypatch):
    source = 'COMMAND_HANDLERS = {"still-found": print}\n\ndef create_argument_parser():\n    raise RuntimeError("needs a database")\n'
    assert _project(tmp_path, monkeypatch, source, name="raisingpkg")._extract_cli_commands() == ["still-found"]


def test_a_module_with_no_parser_factory_falls_back_to_the_scan(tmp_path, monkeypatch):
    source = 'COMMAND_HANDLERS = {"scan-only": print}\n'
    assert _project(tmp_path, monkeypatch, source, name="nofactorypkg")._extract_cli_commands() == ["scan-only"]


def test_an_installed_copy_of_the_module_that_is_not_the_assessed_file_is_not_trusted(tmp_path, monkeypatch):
    """importlib would happily answer for a DIFFERENT checkout of the same package name; only the assessed file may answer."""
    agent = _project(tmp_path, monkeypatch, PARSER_CLI, name="sharedname")
    other = tmp_path / "other"
    (other / "sharedname").mkdir(parents=True)
    (other / "sharedname" / "__init__.py").write_text("")
    (other / "sharedname" / "cli.py").write_text(textwrap.dedent('COMMAND_HANDLERS = {"from-another-copy": print}\n'))
    import importlib
    import sys

    for mod in [m for m in sys.modules if m == "sharedname" or m.startswith("sharedname.")]:
        del sys.modules[mod]
    monkeypatch.syspath_prepend(str(other))  # the other copy now shadows the assessed one on sys.path
    importlib.invalidate_caches()
    commands = agent._extract_cli_commands()
    assert "from-another-copy" not in commands
    assert "alpha-run" in commands  # the scan of the ASSESSED file's own text


def test_this_repository_reports_its_real_command_set_not_help(monkeypatch):
    """The regression itself: on the tree under test the set was {'help'}."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    commands = EpistemicDocsAgent(root)._extract_cli_commands()
    assert len(commands) > 100 and {"preflight-submit", "goals-create", "docs-assess"} <= set(commands)


@pytest.mark.parametrize("verbose", [False, True])
def test_a_failing_introspection_never_raises_even_when_verbose(tmp_path, monkeypatch, verbose):
    source = 'COMMAND_HANDLERS = {"x-cmd": print}\n\ndef create_argument_parser():\n    raise ValueError("boom")\n'
    agent = _project(tmp_path, monkeypatch, source, name=f"verbosepkg{int(verbose)}")
    agent.verbose = verbose
    assert agent._extract_cli_commands() == ["x-cmd"]
