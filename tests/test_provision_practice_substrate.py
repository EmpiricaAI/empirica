"""`provision-practice --no-cortex` records the substrate it actually has.

ecodex-lab (prop_3dyotslsvve4pcarjfrw5g6q5u): `substrate = args.substrate or "cortex"` and the parser defaulted
`--substrate` to cortex, while `--no-cortex` only skipped the Cortex POST in project-register. A standalone
provision therefore wrote project.yaml `substrate: cortex`. The roster documents the values as cortex | git | local,
so a standalone practice is `local`. An explicit `--substrate` always wins.
"""

from __future__ import annotations

import json
import types

import pytest

from empirica.cli.cli_core import create_argument_parser
from empirica.cli.command_handlers import provision_practice_commands as pp


def _args(**over):
    base = {"substrate": None, "no_cortex": False}
    base.update(over)
    return types.SimpleNamespace(**base)


@pytest.mark.parametrize(
    ("substrate", "no_cortex", "expected"),
    [
        (None, False, "cortex"),
        (None, True, "local"),
        ("git", True, "git"),
        ("git", False, "git"),
        ("cortex", True, "cortex"),
    ],
)
def test_the_substrate_follows_no_cortex_unless_given(substrate, no_cortex, expected):
    assert pp._resolve_substrate(_args(substrate=substrate, no_cortex=no_cortex)) == expected


def test_the_parser_no_longer_pre_fills_cortex_so_the_handler_can_tell_given_from_default():
    ns = create_argument_parser().parse_args(["provision-practice", "demo", "--no-cortex"])

    assert ns.substrate is None


def test_a_dry_run_reports_the_resolved_substrate(tmp_path, capsys):
    pp.handle_provision_practice_command(
        types.SimpleNamespace(
            name="demo",
            base_path=str(tmp_path),
            tenant="t",
            org="o",
            substrate=None,
            no_cortex=True,
            dry_run=True,
            output="json",
        )
    )

    out = capsys.readouterr().out
    # --dry-run prints its "would run" lines to stdout ahead of the JSON document; read from the document.
    assert json.loads(out[out.index("{\n") :])["substrate"] == "local"
