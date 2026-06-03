"""MCP server inspection command parsers.

Historical: this module used to register `mcp-start`/`stop`/`status`/
`test`/`call` parsers. Those CLI commands were retired 2026-06-03 — the
MCP server lifecycle is now owned by the harness's MCP config file
(Claude Desktop, Cursor, Gemini CLI, Codex). Only `mcp-list-tools`
remains as a read-only inspection surface.
"""


def add_mcp_parsers(subparsers):
    """Add MCP inspection command parsers."""

    # mcp-list-tools — read the TOOL_REGISTRY from the installed
    # empirica-mcp package and render it (dynamic, not hardcoded).
    mcp_list_tools = subparsers.add_parser(
        'mcp-list-tools',
        help='List MCP tools registered in the installed empirica-mcp package',
        description=(
            'Reads the TOOL_REGISTRY from the empirica-mcp package and '
            'renders one row per registered tool, grouped by prefix. '
            'Useful for debugging "is this tool registered? what CLI '
            'does it route to?" without launching a full MCP session.'
        ),
    )
    mcp_list_tools.add_argument(
        '--verbose', '-v', action='store_true',
        help='Show tip for inspecting per-tool param schemas',
    )
