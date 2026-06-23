"""
Command Handlers Module - Modular CLI Command Implementations

Organizes CLI command handlers by semantic function for maintainability.
"""

# Import all command handlers
from .action_commands import handle_act_log_command, handle_investigate_log_command

# mistake commands consolidated into artifact_log_commands.py
from .artifact_log_commands import (
    handle_assumption_log_command,
    handle_deadend_log_command,
    handle_decision_log_command,
    handle_engagement_focus_command,
    handle_finding_log_command,
    handle_mistake_log_command,
    handle_mistake_query_command,
    handle_source_add_command,
    handle_source_archive_command,
    handle_source_list_command,
    handle_sources_map_command,
    handle_unknown_list_command,
    handle_unknown_log_command,
    handle_unknown_resolve_command,
)
from .artifacts_commands import handle_artifacts_generate_command
from .checkpoint_commands import (
    handle_checkpoint_create_command,
    handle_checkpoint_diff_command,
    handle_checkpoint_list_command,
    handle_checkpoint_load_command,
    handle_efficiency_report_command,
)
from .checkpoint_signing_commands import (
    handle_checkpoint_sign_command,
    handle_checkpoint_signatures_command,
    handle_checkpoint_verify_command,
)
from .cockpit_commands import (
    handle_loop_group_command,
    handle_sentinel_group_command,
)
from .cockpit_commands import (
    handle_status_command as handle_cockpit_status_command,
)
from .cockpit_launcher_commands import (
    handle_cockpit_detach_command,
    handle_cockpit_group_command,
    handle_cockpit_kill_command,
    handle_cockpit_launch_command,
)
from .cockpit_launcher_commands import (
    handle_cockpit_status_command as handle_cockpit_launcher_status_command,
)
from .code_embed import (
    handle_code_embed_command,
)
from .commit_context_commands import handle_commit_context_command
from .compact_analysis import handle_compact_analysis
from .compliance_report_commands import (
    handle_compliance_report_command,
)
from .config_commands import (
    handle_config_command,
    handle_config_get_command,
    handle_config_init_command,
    handle_config_set_command,
    handle_config_show_command,
    handle_config_validate_command,
)
from .diagnose import handle_diagnose_command
from .doc_commands import (
    handle_doc_check_command,
    handle_doc_plan_suggest_command,
)
from .doctor import handle_doctor_command
from .ecosystem_commands import (
    handle_ecosystem_check_command,
)
from .enp_commands import handle_enp_setup_command
from .entity_commands import (
    handle_entity_create_command,
    handle_entity_link_command,
    handle_entity_list_command,
    handle_entity_search_command,
    handle_entity_show_command,
    handle_entity_walk_command,
)
from .epistemics_commands import (
    handle_epistemics_list_command,
    handle_epistemics_search_command,
    handle_epistemics_stats_command,
)
from .epp_commands import (
    handle_epp_activate_command,
)
from .forgejo_commands import handle_forgejo_publish_command
from .git_commands import handle_history_command, handle_save_command
from .goal_commands import (
    handle_goals_activate_command,
    handle_goals_add_dependency_command,
    handle_goals_add_task_command,
    handle_goals_claim_command,
    handle_goals_complete_command,
    handle_goals_complete_task_command,
    handle_goals_create_command,
    handle_goals_discover_command,
    handle_goals_get_stale_command,
    handle_goals_get_tasks_command,
    handle_goals_list_command,
    handle_goals_mark_stale_command,
    handle_goals_progress_command,
    handle_goals_prune_command,
    handle_goals_ready_command,
    handle_goals_refresh_command,
    handle_goals_resume_command,
    handle_goals_search_command,
    handle_sessions_resume_command,
)
from .graph_commands import (
    handle_delete_artifacts_command,
    handle_log_artifacts_command,
    handle_resolve_artifacts_command,
)
from .handoff_commands import handle_handoff_create_command, handle_handoff_query_command
from .identity_commands import (
    handle_identity_create_command,
    handle_identity_export_command,
    handle_identity_list_command,
    handle_identity_verify_command,
)
from .investigation_commands import (
    handle_analyze_command,
    handle_investigate_checkpoint_branch_command,
    handle_investigate_command,
    handle_investigate_create_branch_command,
    handle_investigate_merge_branches_command,
    handle_investigate_multi_command,
)
from .lesson_commands import (
    handle_lesson_create_command,
    handle_lesson_embed_command,
    handle_lesson_list_command,
    handle_lesson_load_command,
    handle_lesson_path_command,
    handle_lesson_recommend_command,
    handle_lesson_replay_end_command,
    handle_lesson_replay_start_command,
    handle_lesson_search_command,
    handle_lesson_stats_command,
)
from .mcp_commands import handle_mcp_list_tools_command
from .module_commands import handle_module_group_command
from .monitor_commands import (
    handle_assess_state_command,
    handle_calibration_dispute_command,
    handle_calibration_report_command,
    handle_mco_load_command,
    handle_monitor_command,
    handle_monitor_cost_command,
    handle_monitor_export_command,
    handle_monitor_reset_command,
    handle_system_status_command,
    handle_trajectory_project_command,
    handle_workflow_patterns_command,
)
from .noetic_batch_commands import handle_noetic_batch_command
from .note_commands import handle_note_command
from .notify_commands import (
    handle_notify_backends_command,
    handle_notify_config_command,
    handle_notify_emit_command,
    handle_notify_group_command,
    handle_notify_test_command,
)
from .onboard import handle_onboard_command
from .performance_commands import handle_benchmark_command, handle_performance_command
from .profile_commands import (
    handle_profile_import_command,
    handle_profile_prune_command,
    handle_profile_status_command,
    handle_profile_sync_command,
)
from .project_bootstrap import handle_project_bootstrap_command
from .project_commands import (
    handle_project_create_command,
    handle_project_handoff_command,
    handle_project_list_command,
    handle_project_switch_command,
)
from .project_embed import (
    handle_project_embed_command,
)
from .project_init import handle_project_init_command
from .project_search import (
    handle_project_search_command,
)
from .project_update import handle_project_update_command
from .projects_commands import (
    handle_daemon_deny_command,
    handle_daemon_grant_command,
    handle_daemon_grants_list_command,
    handle_daemon_list_command,
    handle_project_register_command,
    handle_projects_bulk_register_command,
    handle_projects_discover_command,
    handle_projects_list_command,
    handle_projects_sync_command,
    handle_projects_unregister_command,
)
from .release_commands import handle_release_command
from .scan_commands import (
    handle_scan_command,
    handle_scan_diff_command,
    handle_scan_history_command,
    handle_scan_show_command,
    handle_services_audit_command,
)
from .security_audit_commands import handle_security_audit_command
from .sentinel_commands import (
    handle_sentinel_check_command,
    handle_sentinel_load_profile_command,
    handle_sentinel_orchestrate_command,
    handle_sentinel_status_command,
)
from .serve_commands import handle_serve_command
from .session_commands import (
    handle_memory_compact_command,
    handle_session_snapshot_command,
    handle_sessions_export_command,
    handle_sessions_list_command,
    handle_sessions_show_command,
    handle_transaction_adopt_command,
)
from .session_create import handle_session_create_command
from .setup_claude_code import handle_setup_claude_code_command
from .skill_commands import (
    handle_skill_extract_command,
    handle_skill_fetch_command,
    handle_skill_suggest_command,
)
from .sources_reconcile_commands import handle_sources_reconcile_command
from .sync_commands import (
    handle_rebuild_command,
    handle_sync_config_command,
    handle_sync_pull_command,
    handle_sync_push_command,
    handle_sync_status_command,
)
from .training_commands import (
    handle_training_export_command,
)
from .visibility_commands import (
    handle_visibility_group_command,
    handle_visibility_list_command,
    handle_visibility_show_command,
)

# handle_goal_analysis_command removed - was in noetic_praxic_commands (deprecated)
from .vision_commands import handle_vision_analyze, handle_vision_log
from .voice_commands import (
    handle_voice_apply_command,
    handle_voice_group_command,
    handle_voice_list_command,
    handle_voice_show_command,
)
from .workflow_commands import (
    handle_check_command,
    handle_check_submit_command,
    handle_postflight_submit_command,
    handle_preflight_submit_command,
)
from .workspace_commands import (
    handle_workspace_backfill_entities_command,
    handle_workspace_list_command,
    handle_workspace_map_command,
    handle_workspace_overview_command,
)
from .workspace_init import handle_workspace_init_command
from .workspace_search import handle_workspace_search_command

# Export all handlers — deliberately grouped by domain (comments separate
# semantic clusters), so strict alphabetical sort would lose the structure.
__all__ = [  # noqa: RUF022
    "handle_act_log_command",
    "handle_analyze_command",
    # Artifact generation
    "handle_artifacts_generate_command",
    "handle_assess_state_command",
    "handle_assumption_log_command",
    # Performance commands
    "handle_benchmark_command",
    "handle_calibration_dispute_command",
    "handle_calibration_report_command",
    "handle_check_command",
    "handle_check_submit_command",
    "handle_checkpoint_create_command",
    "handle_checkpoint_diff_command",
    "handle_checkpoint_list_command",
    "handle_checkpoint_load_command",
    # Checkpoint signing commands (Phase 2 - Crypto)
    "handle_checkpoint_sign_command",
    "handle_checkpoint_signatures_command",
    "handle_checkpoint_verify_command",
    "handle_cockpit_detach_command",
    "handle_cockpit_group_command",
    "handle_cockpit_kill_command",
    "handle_cockpit_launch_command",
    "handle_cockpit_launcher_status_command",
    "handle_cockpit_status_command",
    "handle_code_embed_command",
    "handle_commit_context_command",
    "handle_compact_analysis",
    "handle_compliance_report_command",
    # Config commands
    "handle_config_command",
    "handle_config_get_command",
    "handle_config_init_command",
    "handle_config_set_command",
    "handle_config_show_command",
    "handle_config_validate_command",
    "handle_deadend_log_command",
    "handle_decision_log_command",
    "handle_delete_artifacts_command",
    "handle_diagnose_command",
    "handle_doc_check_command",
    "handle_doc_plan_suggest_command",
    "handle_doctor_command",
    "handle_ecosystem_check_command",
    "handle_efficiency_report_command",
    "handle_engagement_focus_command",
    "handle_enp_setup_command",
    "handle_epistemics_list_command",
    # Epistemic trajectory commands
    "handle_epistemics_search_command",
    "handle_epistemics_stats_command",
    "handle_epp_activate_command",
    "handle_finding_log_command",
    "handle_note_command",
    "handle_forgejo_publish_command",
    "handle_goals_activate_command",  # Activate planned goal → in_progress
    "handle_goals_add_dependency_command",
    "handle_goals_add_task_command",
    "handle_goals_claim_command",  # Phase 3a - Git bridge
    "handle_goals_complete_command",  # Phase 3a - Git bridge
    "handle_goals_complete_task_command",
    # NEW: Goal Management Commands (MCP v2 Integration)
    "handle_goals_create_command",
    "handle_goals_discover_command",
    "handle_goals_get_stale_command",  # Get stale goals needing re-evaluation
    "handle_goals_get_tasks_command",
    "handle_goals_list_command",
    "handle_goals_mark_stale_command",  # Pre-compact hook - mark goals stale
    "handle_goals_progress_command",
    "handle_goals_prune_command",
    "handle_goals_ready_command",  # BEADS integration
    "handle_goals_refresh_command",  # Refresh stale goal back to in_progress
    "handle_goals_resume_command",
    "handle_goals_search_command",
    # Handoff Reports commands (Phase 1.6)
    "handle_handoff_create_command",
    "handle_handoff_query_command",
    "handle_history_command",
    # NEW: Identity Management Commands (Phase 2 - EEP-1)
    "handle_identity_create_command",
    "handle_identity_export_command",
    "handle_identity_list_command",
    "handle_identity_verify_command",
    "handle_investigate_checkpoint_branch_command",
    # Investigation commands
    "handle_investigate_command",
    "handle_investigate_create_branch_command",
    # Action commands (INVESTIGATE and ACT phase tracking)
    "handle_investigate_log_command",
    "handle_investigate_merge_branches_command",
    "handle_investigate_multi_command",
    # Lesson commands (Epistemic Procedural Knowledge)
    "handle_lesson_create_command",
    "handle_lesson_embed_command",
    "handle_lesson_list_command",
    "handle_lesson_load_command",
    "handle_lesson_path_command",
    "handle_lesson_recommend_command",
    "handle_lesson_replay_end_command",
    "handle_lesson_replay_start_command",
    "handle_lesson_search_command",
    "handle_lesson_stats_command",
    "handle_log_artifacts_command",
    # Loop / sentinel group dispatchers — re-exported for cli_core wildcard import
    "handle_loop_group_command",
    "handle_module_group_command",
    "handle_mco_load_command",
    # MCP inspection (lifecycle owned by harness mcp.json — 5 commands retired 2026-06-03)
    "handle_mcp_list_tools_command",
    "handle_memory_compact_command",
    # Mistake Logging commands (Learning from Failures)
    "handle_mistake_log_command",
    "handle_mistake_query_command",
    # Monitor commands
    "handle_monitor_command",
    "handle_monitor_cost_command",
    "handle_monitor_export_command",
    "handle_monitor_reset_command",
    "handle_noetic_batch_command",
    # Notify dispatcher commands
    "handle_notify_backends_command",
    "handle_notify_config_command",
    "handle_notify_emit_command",
    "handle_notify_group_command",
    "handle_notify_test_command",
    # Onboarding commands
    "handle_onboard_command",
    "handle_performance_command",
    "handle_postflight_submit_command",
    # NEW: MCP v2 Workflow Commands (Critical Priority)
    "handle_preflight_submit_command",
    "handle_profile_import_command",
    "handle_profile_prune_command",
    "handle_profile_status_command",
    # Profile management
    "handle_profile_sync_command",
    "handle_project_bootstrap_command",
    # Project Tracking commands (Multi-repo/multi-session)
    "handle_project_create_command",
    "handle_project_embed_command",
    "handle_project_handoff_command",
    "handle_project_init_command",
    "handle_project_list_command",
    "handle_project_register_command",
    "handle_project_search_command",
    "handle_project_switch_command",
    "handle_project_update_command",
    "handle_daemon_deny_command",
    "handle_daemon_grant_command",
    "handle_daemon_grants_list_command",
    "handle_daemon_list_command",
    "handle_projects_bulk_register_command",
    "handle_projects_discover_command",
    "handle_projects_list_command",
    "handle_projects_sync_command",
    "handle_projects_unregister_command",
    "handle_rebuild_command",
    "handle_release_command",
    "handle_resolve_artifacts_command",
    "handle_save_command",
    # Scanner — Phase 1 (PROPOSAL_AI_SERVICE_SCANNER.md)
    "handle_scan_command",
    "handle_scan_diff_command",
    "handle_scan_history_command",
    "handle_scan_show_command",
    "handle_security_audit_command",
    "handle_sentinel_check_command",
    "handle_sentinel_group_command",
    "handle_sentinel_load_profile_command",
    # Sentinel orchestration commands
    "handle_sentinel_orchestrate_command",
    "handle_sentinel_status_command",
    # Serve command
    "handle_serve_command",
    "handle_services_audit_command",
    # Checkpoint commands (Phase 2)
    "handle_session_create_command",
    "handle_session_snapshot_command",
    "handle_sessions_export_command",
    "handle_sessions_export_command",
    # Session commands
    "handle_sessions_list_command",
    # Session commands
    "handle_sessions_list_command",
    "handle_sessions_resume_command",
    "handle_sessions_show_command",
    "handle_sessions_show_command",
    "handle_setup_claude_code_command",
    "handle_skill_extract_command",
    "handle_skill_fetch_command",
    "handle_skill_suggest_command",
    "handle_source_add_command",
    "handle_source_archive_command",
    "handle_source_list_command",
    "handle_sources_map_command",
    "handle_sources_reconcile_command",
    # Sync commands (git notes synchronization)
    "handle_sync_config_command",
    "handle_sync_pull_command",
    "handle_sync_push_command",
    "handle_sync_status_command",
    "handle_system_status_command",
    "handle_training_export_command",
    "handle_trajectory_project_command",
    "handle_transaction_adopt_command",
    "handle_unknown_list_command",
    "handle_unknown_log_command",
    "handle_unknown_resolve_command",
    # Visibility tier commands (PROPOSAL_VISIBILITY_TIERS.md)
    "handle_visibility_group_command",
    "handle_visibility_list_command",
    "handle_visibility_show_command",
    # Vision commands
    "handle_vision_analyze",
    "handle_vision_log",
    # Voice profile commands
    "handle_voice_apply_command",
    "handle_voice_group_command",
    "handle_voice_list_command",
    "handle_voice_show_command",
    "handle_workflow_patterns_command",
    "handle_workspace_backfill_entities_command",
    "handle_workspace_init_command",
    "handle_workspace_list_command",
    "handle_workspace_map_command",
    "handle_workspace_overview_command",
    # Workspace search + engagement focus + git abstractions
    "handle_workspace_search_command",
    # Entity registry CLI (Practice Model backing)
    "handle_entity_create_command",
    "handle_entity_link_command",
    "handle_entity_list_command",
    "handle_entity_search_command",
    "handle_entity_show_command",
    "handle_entity_walk_command",
    # Session-end command
    # 'handle_session_end_command',  # removed - use handoff-create
]
