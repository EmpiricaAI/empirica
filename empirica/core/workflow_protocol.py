"""The workflow protocol's schema: one definition for /ewm-interview, the web onboarding interview and the installer.

`empirica/schemas/workflow_protocol.schema.json` is the contract (David's rulings, 2026-11-17: one person's protocol, closed,
values fixed to what the loader can describe, versioned by a required ``schema_version``). Cortex vendors the file and
validates the web interview's structured output against it; ``provision-practice --from-protocol`` validates before it
provisions anything.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "workflow_protocol.schema.json"
SCHEMA_VERSION = "0.3.0"
SCHEMA_ID = f"https://getempirica.com/schemas/workflow-protocol/{SCHEMA_VERSION}.json"


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(document: Any) -> list[str]:
    """Every way ``document`` breaks the schema, as ``path: message`` lines; empty when it is valid."""
    from jsonschema import Draft202012Validator, FormatChecker

    validator = Draft202012Validator(schema(), format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    return [f"{'.'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message}" for e in errors]


def load(path: str | Path) -> tuple[dict | None, list[str]]:
    """(the protocol, its problems) from a YAML or JSON file. A file that cannot be read or parsed is a problem, not a
    crash; a document with problems is still returned so a caller can show it."""
    import yaml

    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as e:
        return None, [f"cannot read {path}: {e}"]
    try:
        document = json.loads(text) if str(path).endswith(".json") else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as e:
        return None, [f"cannot parse {path}: {e}"]
    if not isinstance(document, dict):
        return None, [f"{path} does not hold a protocol (expected a mapping)"]
    document = _dates_as_text(document)
    return document, validate(document)


def _dates_as_text(value: Any) -> Any:
    """YAML reads an unquoted 2026-11-17 as a date; the schema (and JSON) carry it as text."""
    import datetime as _dt

    if isinstance(value, dict):
        return {k: _dates_as_text(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_dates_as_text(v) for v in value]
    if isinstance(value, _dt.date):
        return value.isoformat()
    return value
