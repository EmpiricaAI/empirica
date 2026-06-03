"""MCP Command Handlers — inspection only.

Historical note: this module used to ship CLI-driven MCP lifecycle
management (`empirica mcp-start`/`stop`/`status`/`test`/`call`). Those
commands targeted a `mcp_local/empirica_mcp_server.py` path that no
longer exists — the MCP server moved to its own package
(`empirica-mcp`, installed via pipx as the `empirica-mcp` console
script). Lifecycle ownership is now in the harness's MCP config
(Claude Desktop, Cursor, Gemini CLI, Codex `mcp.json` files), not in
empirica's CLI. The CLI commands were removed 2026-06-03.

What remains: `mcp-list-tools` — read-only inspection of the
TOOL_REGISTRY in the installed `empirica-mcp` package, useful for
debugging "is this tool registered? what params does it take?" from
the same shell session.
"""

import sys

from ..cli_utils import handle_cli_error, print_header


def handle_mcp_list_tools_command(args):
    """List MCP tools by reading TOOL_REGISTRY from the installed empirica-mcp package.

    Dynamic — reflects what the running MCP server would actually
    expose, not a hardcoded snapshot. Falls back to a helpful error if
    empirica-mcp isn't importable (not installed, or wrong venv).
    """
    try:
        try:
            from empirica_mcp.server import TOOL_REGISTRY
        except ImportError:
            print("❌ empirica-mcp package not importable in this Python.")
            print("   Install: pipx install empirica-mcp  (or `pip install empirica[mcp]`)")
            print("   Verify:  empirica-mcp --help")
            return 1

        total = len(TOOL_REGISTRY)
        cortex_required = sum(1 for e in TOOL_REGISTRY.values() if e.get("requires"))
        standalone = total - cortex_required
        print_header(
            f"🔧 Empirica MCP Tools ({total} registered — "
            f"{standalone} standalone, {cortex_required} cortex-orchestrated)"
        )

        # Group by best-effort category prefix on the tool name
        groups: dict[str, list[tuple[str, str, str]]] = {}
        for tool_name, entry in TOOL_REGISTRY.items():
            cli = entry.get("cli", "?")
            desc = entry.get("desc", "")
            requires = entry.get("requires", "")
            prefix = tool_name.split("_", 1)[0]
            groups.setdefault(prefix, []).append((tool_name, f"{cli} — {desc}", requires))

        for prefix in sorted(groups):
            print(f"\n{prefix}:")
            for tool_name, summary, requires in sorted(groups[prefix]):
                marker = "  🌐" if requires else "    "
                print(f"{marker}{tool_name:38s} {summary[:90]}")

        verbose = getattr(args, "verbose", False)
        if cortex_required:
            print("\n🌐 = requires cortex (mesh backend). Base empirica works standalone;")
            print("   these tools surface 'cortex config missing' until you configure it.")
            print("   See docs/human/end-users/MCP_FOR_DESKTOP_HARNESSES.md.")

        if verbose:
            print("\n💡 Tool params + required fields — read the TOOL_REGISTRY entries directly:")
            print(f"   {sys.executable} -c 'from empirica_mcp.server import TOOL_REGISTRY; "
                  "import json; print(json.dumps(TOOL_REGISTRY[\"finding_log\"], indent=2))'")
            print("💡 See docs/human/developers/MCP_SERVER_REFERENCE.md for usage")

        return 0
    except Exception as e:
        handle_cli_error(e, "Listing MCP tools", getattr(args, "verbose", False))
        return 1
