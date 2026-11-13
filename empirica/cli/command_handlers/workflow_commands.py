"""Workflow Commands — MCP v2 Integration Commands.

This module is now a re-export shim. The actual implementation lives in
four sibling modules (split in v1.9.6+ follow-up to keep each module
under 1500 LOC):

    _workflow_shared     — cross-cutting helpers (db, session resolution,
                           sentinel hook invocation, retrospective counters,
                           vector normalization, noetic/voice guidance)
    _workflow_preflight  — handle_preflight_submit_command and its helpers
    _workflow_check      — handle_check_command + handle_check_submit_command
    _workflow_postflight — handle_postflight_submit_command + storage pipeline
                           + grounded verification + compliance loop

External callers (CLI dispatcher, tests, plugins) can keep importing from
`workflow_commands` as before. Helpers below the public handler line are
re-exported pragmatically because the existing test suite reaches into
them — preserved here to avoid a test-rewrite cycle. New callers should
import directly from the phase modules.
"""

# Public handlers — the four CLI entry points.
from ._workflow_check import handle_check_command, handle_check_submit_command

# Internal helpers reached into by the existing test suite. Keep these
# re-exports stable; deprecate gradually as tests are rewritten to import
# from the phase modules directly.
from ._workflow_postflight import (
    _build_postflight_result,
    _cortex_resolve_project_id,
    _postflight_parse_config_or_legacy,
    _validate_postflight_preconditions,
    handle_postflight_submit_command,
)
from ._workflow_preflight import handle_preflight_submit_command
from ._workflow_shared import (
    _build_retrospective,
    _build_voice_guidance,
    _extract_numeric_value,
    _soft_run,
)

__all__ = [
    "_build_postflight_result",
    "_build_retrospective",
    "_build_voice_guidance",
    "_cortex_resolve_project_id",
    "_extract_numeric_value",
    "_postflight_parse_config_or_legacy",
    "_soft_run",
    "_validate_postflight_preconditions",
    "handle_check_command",
    "handle_check_submit_command",
    "handle_postflight_submit_command",
    "handle_preflight_submit_command",
]
