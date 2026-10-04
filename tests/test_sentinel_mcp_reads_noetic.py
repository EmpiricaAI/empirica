"""Pure-read MCP tools are noetic in every phase, from one predicate.

cowork (prop_fmymqnc5zze2nbclxoyfue45yq), seen by ecodex-lab: `mcp__cortex__cortex_list_goals` was refused after
POSTFLIGHT with "Epistemic loop closed ... Run new PREFLIGHT", although its AGENTS.md says noetic reads are allowed in any
phase. Cause: ten pure-read cortex tools were not in NOETIC_MCP_CORTEX, so the gate treated them as praxic; and the
four-part "is this tool noetic" predicate was copied out at seven sites, which is how a set that grew in one place
stayed stale in the sense nobody could audit it. The CRM server's read tools were in the same state.

One helper, `_is_noetic_tool`, is now the only place the sets are consulted.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"
LIB = HOOKS.parent / "lib"
GATE = HOOKS / "sentinel-gate.py"


@pytest.fixture(scope="module")
def gate():
    sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location("sentinel_gate_mcp_reads", GATE)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


CORTEX_READS = [
    "cortex_list_goals",
    "cortex_list_artifacts",
    "cortex_list_sources",
    "cortex_memory_status",
    "cortex_ai_discover",
    "cortex_get_skill",
    "cortex_list_scheduled_skills",
    "cortex_source_chunks",
    "cortex_source_get_raw",
    "cortex_render_skill_board",
]

CRM_READS = [
    "crm_whoami",
    "crm_schema",
    "crm_get",
    "crm_list_organizations",
    "crm_list_contacts",
    "crm_list_engagements",
    "crm_list_touchpoints",
    "crm_list_revenue_events",
    "crm_scope_changes",
    "crm_consent_state",
]

# Every one of these changes state somewhere: they stay gated.
CORTEX_WRITES = [
    "cortex_propose",
    "cortex_publish",
    "cortex_emit_system_event",
    "cortex_delete_artifacts",
    "cortex_delete_proposal",
    "cortex_resolve_artifacts",
    "cortex_goal_update",
    "cortex_schedule_skill",
    "cortex_cancel_scheduled_skill",
    "cortex_transition_ser",
    "cortex_ser_ack",
    "cortex_retarget_proposal",
    "cortex_project_register",
    "cortex_skill_governance",
    "cortex_bug_report",
    "cortex_ingest_content",
    "cortex_source_set_visibility",
    "cortex_source_archive",
]

CRM_WRITES = [
    "crm_upsert_organization",
    "crm_upsert_contact",
    "crm_upsert_engagement",
    "crm_upsert_touchpoint",
    "crm_upsert_revenue_event",
    "crm_delete_touchpoint",
    "crm_link_contact",
    "crm_unlink_contact",
    "crm_set_scope",
    "crm_set_created_at",
    "crm_transfer_owner",
    "crm_supersede_organization",
    "crm_supersede_contact",
    "crm_supersede_engagement",
    "crm_follow_supersession",
    "crm_record_consent_event",
]


def _noetic(gate, tool: str) -> bool:
    return gate._noetic_firewall_check(tool, {}, {}) is not None


@pytest.mark.parametrize("op", CORTEX_READS)
def test_a_pure_read_cortex_tool_is_noetic(gate, op):
    assert _noetic(gate, f"mcp__cortex__{op}")


@pytest.mark.parametrize("op", CRM_READS)
def test_a_pure_read_crm_tool_is_noetic(gate, op):
    assert _noetic(gate, f"mcp__empirica-crm__{op}")


@pytest.mark.parametrize("op", CORTEX_WRITES)
def test_a_cortex_tool_that_changes_state_stays_gated(gate, op):
    assert not _noetic(gate, f"mcp__cortex__{op}")


@pytest.mark.parametrize("op", CRM_WRITES)
def test_a_crm_tool_that_changes_state_stays_gated(gate, op):
    assert not _noetic(gate, f"mcp__empirica-crm__{op}")


def test_an_unknown_tool_under_either_server_is_gated(gate):
    """A tool nobody has classified is praxic until someone reads it."""
    assert not _noetic(gate, "mcp__cortex__cortex_brand_new_tool")
    assert not _noetic(gate, "mcp__empirica-crm__crm_brand_new_tool")


def test_an_aggregated_namespace_call_resolves_to_the_read_and_is_noetic(gate):
    """ecodex presents the bare `mcp__cortex` namespace with the operation in the input."""
    name = gate._normalize_aggregated_cortex_tool("mcp__cortex", {"op": "cortex_list_goals"})

    assert name == "mcp__cortex__cortex_list_goals" and _noetic(gate, name)


def test_the_noetic_sets_are_consulted_in_exactly_one_place():
    """Seven copies of the predicate is how a set stays stale where nobody audits it. One helper reads the sets."""
    src = GATE.read_text()

    assert len(re.findall(r"tool_name in NOETIC_MCP_CORTEX", src)) == 1
    assert len(re.findall(r"tool_name in NOETIC_MCP_CHROME", src)) == 1
    assert len(re.findall(r"tool_name in NOETIC_TOOLS", src)) == 1
    assert "def _is_noetic_tool(" in src


def test_the_helper_agrees_with_the_firewall_for_the_older_tools(gate):
    """Control: nothing that was noetic before the refactor stopped being so."""
    for tool in (
        "Read",
        "Grep",
        "mcp__cortex__investigate",
        "mcp__cortex__cortex_inbox_poll",
        "mcp__empirica__investigate",
    ):
        assert gate._is_noetic_tool(tool), tool
    assert not gate._is_noetic_tool("Write") and not gate._is_noetic_tool("Edit")
