"""
CLI Parser Modules - Modularized argument parsers for Empirica CLI

Each module contains parser definitions for a specific command group.
This modularization makes the CLI more maintainable by breaking down
the monolithic cli_core.py (1176 lines) into focused modules.

Help Text Guidelines:
- Required arguments: Include "(required)" in help text
- Optional arguments: Include "(optional)" or explain default behavior
- Example: '--session-id SESSION_ID (required)'
- Example: '--limit LIMIT (optional, default: 10)'
"""


def format_help_text(text, required=False, default=None):
    """
    Format help text with clear required/optional markers.

    Args:
        text: Base help text
        required: If True, add (required) marker
        default: If provided, add default value info

    Returns:
        Formatted help text

    Examples:
        format_help_text("Session ID", required=True)
        # Returns: "Session ID (required)"

        format_help_text("Maximum items", default=10)
        # Returns: "Maximum items (optional, default: 10)"
    """
    if required:
        result = f"{text} (required)"
    elif default is not None:
        result = f"{text} (optional, default: {default})"
    else:
        result = f"{text} (optional)"
    # Escape % for Python 3.14+ argparse, which interprets % as format specifier in help strings
    return result.replace("%", "%%")


from .architecture_parsers import add_architecture_parsers
from .bus_parsers import add_bus_parsers
from .cascade_parsers import add_cascade_parsers
from .chat_parsers import add_chat_parsers
from .checkpoint_parsers import add_checkpoint_parsers
from .cockpit_launcher_parsers import add_cockpit_launcher_parsers
from .cockpit_parsers import add_cockpit_parsers
from .concept_graph_parsers import add_concept_graph_parsers
from .config_parsers import add_config_parsers
from .domain_parsers import add_domain_parsers, add_resolve_parser
from .edit_verification_parsers import add_edit_verification_parsers
from .epistemics_parsers import add_epistemics_parsers
from .investigation_parsers import add_investigation_parsers
from .issue_capture_parsers import add_issue_capture_parsers
from .lesson_parsers import add_lesson_parsers
from .mailbox_parsers import add_mailbox_parsers
from .mcp_parsers import add_mcp_parsers
from .memory_parsers import add_memory_parsers
from .mesh_parsers import add_mesh_parsers
from .message_parsers import add_message_parsers
from .module_parsers import add_module_parsers
from .monitor_parsers import add_monitor_parsers
from .notify_parsers import add_notify_parsers
from .onboarding_parsers import add_onboarding_parsers
from .performance_parsers import add_performance_parsers
from .profile_parsers import add_profile_parsers
from .projects_parsers import add_projects_parsers
from .provision_practice_parsers import add_provision_practice_parsers
from .query_parsers import add_query_parsers
from .release_parsers import add_release_parsers
from .scan_parsers import add_scan_parsers
from .sentinel_parsers import add_sentinel_parsers
from .serve_parsers import add_serve_parsers
from .session_parsers import add_session_parsers
from .skill_parsers import add_skill_parsers
from .trajectory_parsers import add_trajectory_parsers
from .user_interface_parsers import add_user_interface_parsers
from .utility_parsers import add_utility_parsers
from .visibility_parsers import add_visibility_parsers
from .vision_parsers import add_vision_parsers
from .voice_parsers import add_voice_parsers

__all__ = [
    "add_architecture_parsers",
    "add_bus_parsers",
    "add_cascade_parsers",
    "add_chat_parsers",
    "add_checkpoint_parsers",
    "add_cockpit_launcher_parsers",
    "add_cockpit_parsers",
    "add_concept_graph_parsers",
    "add_config_parsers",
    "add_domain_parsers",
    "add_edit_verification_parsers",
    "add_epistemics_parsers",
    "add_investigation_parsers",
    "add_issue_capture_parsers",
    "add_lesson_parsers",
    "add_mailbox_parsers",
    "add_mcp_parsers",
    "add_memory_parsers",
    "add_mesh_parsers",
    "add_message_parsers",
    "add_module_parsers",
    "add_monitor_parsers",
    "add_notify_parsers",
    "add_onboarding_parsers",
    "add_performance_parsers",
    "add_profile_parsers",
    "add_projects_parsers",
    "add_provision_practice_parsers",
    "add_query_parsers",
    "add_release_parsers",
    "add_resolve_parser",
    "add_scan_parsers",
    "add_sentinel_parsers",
    "add_serve_parsers",
    "add_session_parsers",
    "add_skill_parsers",
    "add_trajectory_parsers",
    "add_user_interface_parsers",
    "add_utility_parsers",
    "add_visibility_parsers",
    "add_vision_parsers",
    "add_voice_parsers",
    "format_help_text",
]
