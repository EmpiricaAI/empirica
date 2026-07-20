"""Guards for `provision-practice`'s optional forgejo-backup wiring.

Regression for the shipped-default infra leak: the `--forgejo-host` arg
carried a hardcoded private Hetzner SSH URL as its default, so any user
passing `--forgejo-owner` without `--forgejo-host` would point their
backup remote at someone else's server. The host must come from the flag
or the EMPIRICA_FORGEJO_HOST env var — never a built-in default.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from empirica.cli.parsers.provision_practice_parsers import add_provision_practice_parsers

_SRC = Path(__file__).parent.parent / "empirica" / "cli"
_PARSER_SRC = _SRC / "parsers" / "provision_practice_parsers.py"
_HANDLER_SRC = _SRC / "command_handlers" / "provision_practice_commands.py"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_provision_practice_parsers(subparsers)
    return parser


def test_no_hardcoded_host_in_source():
    """No IPv4 literal may appear in either provision-practice source file —
    the shipped default must never re-embed a specific server."""
    ipv4 = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
    for src in (_PARSER_SRC, _HANDLER_SRC):
        assert not ipv4.search(src.read_text()), f"hardcoded IP resurfaced in {src.name}"


def test_forgejo_host_is_none_without_flag_or_env(monkeypatch):
    monkeypatch.delenv("EMPIRICA_FORGEJO_HOST", raising=False)
    ns = _build_parser().parse_args(["provision-practice", "foo", "--forgejo-owner", "bar"])
    assert ns.forgejo_host is None  # no built-in default


def test_forgejo_host_reads_env(monkeypatch):
    monkeypatch.setenv("EMPIRICA_FORGEJO_HOST", "ssh://git@example.test:2222")
    # default is bound at add_argument() time, so build the parser AFTER setenv
    ns = _build_parser().parse_args(["provision-practice", "foo", "--forgejo-owner", "bar"])
    assert ns.forgejo_host == "ssh://git@example.test:2222"


def test_forgejo_flag_overrides_env(monkeypatch):
    monkeypatch.setenv("EMPIRICA_FORGEJO_HOST", "ssh://git@env.test:2222")
    ns = _build_parser().parse_args(
        ["provision-practice", "foo", "--forgejo-owner", "bar", "--forgejo-host", "ssh://git@flag.test:22"]
    )
    assert ns.forgejo_host == "ssh://git@flag.test:22"


def test_base_path_default_is_generic():
    """--base-path must default to a product-namespaced dir, not a
    repo-org-specific layout (`~/empirical-ai` was David's convention)."""
    ns = _build_parser().parse_args(["provision-practice", "foo"])
    assert ns.base_path == "~/empirica"
    for src in (_PARSER_SRC, _HANDLER_SRC):
        assert "empirical-ai" not in src.read_text(), f"repo-org layout path resurfaced in {src.name}"
