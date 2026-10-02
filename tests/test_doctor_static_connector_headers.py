"""doctor lists MCP connectors that store a literal Authorization header, by name and never by value.

A stored bearer is a credential that a key rotation signs out (2026-09-29). `headersHelper` with
`empirica auth token --headers` presents the seat's own OAuth token fresh on each connection.
Built under tmp_path; the machine's ~/.claude.json is never read.
"""

from __future__ import annotations

import json

from empirica.cli.command_handlers.doctor import PASS, SKIP, WARN, check_static_connector_headers

SECRET = "sk-live-SUPERSECRETVALUE0123456789"  # noqa: S105 - a fake value the tests assert never reaches a report


def _write(home, config):
    (home).mkdir(parents=True, exist_ok=True)
    (home / ".claude.json").write_text(json.dumps(config))


def test_a_user_scope_connector_with_a_literal_bearer_is_listed_by_name_only(tmp_path):
    """POSITIVE CONTROL: the real shape on this seat."""
    _write(
        tmp_path,
        {
            "mcpServers": {
                "empirica-crm": {
                    "type": "http",
                    "url": "https://crm.example",
                    "headers": {"Authorization": f"Bearer {SECRET}"},
                },
                "cortex": {
                    "type": "http",
                    "url": "https://cortex.example",
                    "headersHelper": "empirica auth token --headers",
                },
            }
        },
    )

    c = check_static_connector_headers(tmp_path)

    assert c.status == WARN and c.data["connectors"] == ["user:empirica-crm"]
    assert "user:empirica-crm" in c.detail and "headersHelper" in c.hint
    assert SECRET not in json.dumps([c.detail, c.hint, c.data]), "a credential must never reach the report"


def test_a_connector_on_headers_helper_passes(tmp_path):
    """CONTROL: the switched connector is not flagged."""
    _write(tmp_path, {"mcpServers": {"cortex": {"type": "http", "headersHelper": "empirica auth token --headers"}}})

    assert check_static_connector_headers(tmp_path).status == PASS


def test_an_environment_reference_is_not_a_stored_secret(tmp_path):
    _write(tmp_path, {"mcpServers": {"x": {"type": "http", "headers": {"Authorization": "Bearer ${CORTEX_TOKEN}"}}}})

    assert check_static_connector_headers(tmp_path).status == PASS


def test_the_header_name_is_matched_case_insensitively(tmp_path):
    _write(tmp_path, {"mcpServers": {"x": {"type": "http", "headers": {"authorization": f"Bearer {SECRET}"}}}})

    assert check_static_connector_headers(tmp_path).data["connectors"] == ["user:x"]


def test_other_headers_are_not_authorization(tmp_path):
    _write(tmp_path, {"mcpServers": {"x": {"type": "http", "headers": {"X-Trace": "abc"}}}})

    assert check_static_connector_headers(tmp_path).status == PASS


def test_project_scope_connectors_are_read_too(tmp_path):
    _write(
        tmp_path,
        {
            "mcpServers": {},
            "projects": {"/work/nle": {"mcpServers": {"crm": {"headers": {"Authorization": f"Bearer {SECRET}"}}}}},
        },
    )

    c = check_static_connector_headers(tmp_path)

    assert c.data["connectors"] == ["project nle:crm"] and SECRET not in c.detail


def test_a_missing_file_is_a_skip_and_a_malformed_one_a_warning_not_a_pass(tmp_path):
    assert check_static_connector_headers(tmp_path).status == SKIP
    (tmp_path / ".claude.json").write_text("{not json")
    assert check_static_connector_headers(tmp_path).status == WARN
    (tmp_path / ".claude.json").write_text("[1, 2]")
    assert check_static_connector_headers(tmp_path).status == WARN


def test_odd_shapes_do_not_crash_the_check(tmp_path):
    _write(
        tmp_path,
        {"mcpServers": {"a": 5, "b": {"headers": "x"}, "c": {"headers": {"Authorization": 7}}}, "projects": {"/p": 3}},
    )

    assert check_static_connector_headers(tmp_path).status == PASS
