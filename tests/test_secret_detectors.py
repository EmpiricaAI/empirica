"""The secret scanner could not see our own credentials — a pass meaning "none I can detect".

`compliance-report --security` runs trufflehog. It reported `unavailable — tool
not installed`, which is honest. Installing the tool alone would have made it
report **PASS**, which is strictly worse: a pass reads as *no secrets* and means
*no secrets I have a detector for*.

Measured: a LIVE cortex admin key (`ctx_empirica_adm_<32 hex>`, verified HTTP 200)
sat in 9 artifacts of this practice's graph, and the stock detector set scored it
**0 findings**. So a check mapped to EU AI Act Art. 15(4) and GDPR Art. 32 would
have gone green over a live credential.

These tests are the positive control for `security/trufflehog-detectors.yaml`.
Without them the file is a list of patterns nobody has ever seen match, which is
the same unfalsifiable-clean it exists to prevent — one layer in.

The regexes are asserted directly rather than by shelling out to trufflehog, so
the suite stays green on a box without the binary. The wiring (that
compliance-report passes `--config` at all) is asserted separately and
structurally, because that is the part a refactor silently drops.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "security" / "trufflehog-detectors.yaml"


@pytest.fixture(scope="module")
def detectors() -> dict[str, re.Pattern]:
    data = yaml.safe_load(CONFIG.read_text())
    out = {}
    for d in data["detectors"]:
        out[d["name"]] = re.compile(d["regex"]["key"])
    return out


def test_the_config_exists_and_declares_detectors(detectors):
    assert detectors, "no detectors declared — the scan would run stock-only"


# ── the credential that was actually live ────────────────────────────────────


def test_the_cortex_admin_key_format_is_detected(detectors):
    """THE case. This exact shape was live and invisible to the stock detectors."""
    sample = "ctx_empirica_adm_" + "a" * 32
    assert detectors["empirica_cortex_api_key"].search(f'key = "{sample}"')


@pytest.mark.parametrize("role", ["adm", "usr", "svc", "readonly"])
def test_any_role_slug_is_detected(detectors, role):
    """Loose on the role, strict on the payload: a new role ships without editing
    this file, which is what stops the detector rotting behind the key format."""
    assert detectors["empirica_cortex_api_key"].search(f"ctx_empirica_{role}_{'b' * 32}")


def test_the_env_var_form_is_detected(detectors):
    """How it reaches CI and shell history, which is a different surface from
    source and needs its own pattern."""
    assert detectors["empirica_cortex_bearer_env"].search('CORTEX_API_KEY="ctx_abcdefgh12345678"')
    assert detectors["empirica_cortex_bearer_env"].search("CORTEX_API_KEY=ctx_abcdefgh12345678")


def test_the_ntfy_token_format_is_detected(detectors):
    """The other credential in credentials.yaml — the one a listener seat carries."""
    assert detectors["empirica_ntfy_token"].search("token: tk_" + "c" * 30)


# ── negative controls, which are what keep the detector usable ───────────────


@pytest.mark.parametrize(
    "text",
    [
        "This finding describes ctx_empirica prefixed keys in prose, with no value.",
        "the ctx_empirica_adm_ prefix appears in incident write-ups",
        "ordinary text about tokens and api keys and secrets",
        "ctx_empirica_adm_tooshort",
        "tk_short",
    ],
    ids=["prose", "bare-prefix", "generic-words", "truncated-key", "short-token"],
)
def test_prose_about_credentials_does_not_fire(detectors, text):
    """A detector that fires on every incident write-up gets disabled rather than
    obeyed — and this graph contains many write-ups ABOUT the key, which must stay
    distinguishable from the key.
    """
    assert not any(p.search(text) for p in detectors.values()), f"false positive on: {text!r}"


# ── the wiring, which is what a refactor drops silently ──────────────────────


def test_compliance_report_passes_the_config_to_trufflehog():
    """Structural. The detectors are worthless if the scan never loads them, and
    that failure is invisible — the scan still runs and still reports a number."""
    src = (ROOT / "empirica/cli/command_handlers/compliance_report_commands.py").read_text()
    assert "trufflehog-detectors.yaml" in src
    assert '"--config"' in src


def test_a_missing_config_still_runs_the_stock_scan():
    """Degradation direction. A fork or partial checkout without the file must get
    the built-in detectors, not no scan at all."""
    src = (ROOT / "empirica/cli/command_handlers/compliance_report_commands.py").read_text()
    assert "detector_config.exists()" in src
