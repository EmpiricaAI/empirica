"""The schema `log-artifacts --schema` prints must state the visibility default the data layer applies (ecodex, 2026-10-23).

The schema advertised "default 'shared'" while `data/visibility.py` resolves an unset visibility to 'local'. A reader following the
printed schema would believe their artifacts were visible to peers when they stayed in the project. The text is compared with the
constant, so changing the default without the schema (or the reverse) fails here instead of in a peer's pre-publish sweep.
"""

from __future__ import annotations

import re

from empirica.cli.command_handlers.graph_commands import LOG_ARTIFACTS_SCHEMA
from empirica.data.visibility import DEFAULT_VISIBILITY, normalize_visibility


def _advertised_default() -> str:
    text = LOG_ARTIFACTS_SCHEMA["nodes"][0]["data"]["visibility"]
    match = re.search(r"default '([a-z]+)'", text)
    assert match, f"the schema no longer states a default: {text!r}"
    return match.group(1)


def test_the_schema_names_the_default_the_data_layer_applies():
    assert _advertised_default() == DEFAULT_VISIBILITY


def test_an_unset_visibility_resolves_to_what_the_schema_says():
    assert normalize_visibility(None) == _advertised_default()
