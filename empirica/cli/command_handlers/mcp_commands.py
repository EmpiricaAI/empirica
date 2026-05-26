"""
MCP Command Handlers - Manage MCP server from CLI

Provides commands for:
- Starting/stopping MCP server
- Checking MCP server status
- Testing MCP connection
- Listing available MCP tools
- Calling MCP tools directly
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from ..cli_utils import handle_cli_error, print_header

# MCP server paths
MCP_SERVER_PATH = Path(__file__).parent.parent.parent.parent / "mcp_local" / "empirica_mcp_server.py"
MCP_PID_FILE = Path.home() / ".empirica" / "mcp_server.pid"


def handle_mcp_start_command(args):
    """Start MCP server in background"""
    try:
        print_header("🚀 Starting Empirica MCP Server")

        # Check if already running
        if _is_mcp_running():
            pid = _get_mcp_pid()
            print(f"✅ MCP server already running (PID: {pid})")
            return

        # Ensure .empirica directory exists
        MCP_PID_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Start MCP server
        python_exe = sys.executable
        log_file = MCP_PID_FILE.parent / "mcp_server.log"

        with open(log_file, 'w') as log:
            process = subprocess.Popen(
                [python_exe, str(MCP_SERVER_PATH)],
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True
            )

        # Save PID
        with open(MCP_PID_FILE, 'w') as f:
            f.write(str(process.pid))

        # Wait a bit to check if it started successfully
        time.sleep(1)

        if _is_mcp_running():
            print(f"✅ MCP server started successfully (PID: {process.pid})")
            print(f"📝 Logs: {log_file}")
            print("\n💡 Configure your IDE to use this MCP server:")
            print(f"   Command: {python_exe}")
            print(f"   Args: [\"{MCP_SERVER_PATH}\"]")
        else:
            print(f"❌ MCP server failed to start. Check logs: {log_file}")

    except Exception as e:
        handle_cli_error(e, "Starting MCP server", getattr(args, 'verbose', False))


def handle_mcp_stop_command(args):
    """Stop MCP server"""
    try:
        print_header("🛑 Stopping Empirica MCP Server")

        if not _is_mcp_running():
            print("ℹ️  MCP server is not running")
            return

        pid = _get_mcp_pid()

        # Try graceful shutdown first
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)

            # Check if stopped
            if not _is_mcp_running():
                print(f"✅ MCP server stopped gracefully (PID: {pid})")
                MCP_PID_FILE.unlink(missing_ok=True)
                return
        except ProcessLookupError:
            pass

        # Force kill if still running
        try:
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.5)
            print(f"✅ MCP server force stopped (PID: {pid})")
        except ProcessLookupError:
            print("ℹ️  MCP server already stopped")

        MCP_PID_FILE.unlink(missing_ok=True)

    except Exception as e:
        handle_cli_error(e, "Stopping MCP server", getattr(args, 'verbose', False))


def handle_mcp_status_command(args):
    """Check MCP server status"""
    try:
        print_header("📊 Empirica MCP Server Status")

        if _is_mcp_running():
            pid = _get_mcp_pid()
            print("✅ Status: Running")
            print(f"🆔 PID: {pid}")
            print(f"📝 Log file: {MCP_PID_FILE.parent / 'mcp_server.log'}")

            if args.verbose:
                # Show process info
                try:
                    import psutil
                    proc = psutil.Process(pid)
                    print("\n📈 Process Info:")
                    print(f"   CPU: {proc.cpu_percent(interval=0.1):.1f}%")
                    print(f"   Memory: {proc.memory_info().rss / 1024 / 1024:.1f} MB")
                    print(f"   Threads: {proc.num_threads()}")
                    print(f"   Created: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(proc.create_time()))}")
                except ImportError:
                    print("\n💡 Install psutil for detailed process info: pip install psutil")
        else:
            print("❌ Status: Not Running")
            print("\n💡 Start with: empirica mcp start")

    except Exception as e:
        handle_cli_error(e, "Checking MCP server status", getattr(args, 'verbose', False))


def handle_mcp_test_command(args):
    """Test MCP server connection"""
    try:
        print_header("🧪 Testing Empirica MCP Server")

        if not _is_mcp_running():
            print("❌ MCP server is not running")
            print("💡 Start with: empirica mcp start")
            return

        print("✅ MCP server is running")
        print("\n🔍 Testing MCP protocol...")

        # Try to import MCP client and test connection
        try:
            # Basic test: Check if server responds to stdio
            python_exe = sys.executable
            result = subprocess.run(
                [python_exe, str(MCP_SERVER_PATH), "--test"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                print("✅ MCP protocol test passed")
            else:
                print(f"⚠️  MCP protocol test returned code {result.returncode}")
                if args.verbose:
                    print(f"\nStdout: {result.stdout}")
                    print(f"Stderr: {result.stderr}")
        except subprocess.TimeoutExpired:
            print("⚠️  MCP server not responding (timeout)")
        except Exception as e:
            print(f"⚠️  MCP test failed: {e}")

        print("\n💡 To test from your IDE, configure MCP server:")
        print(f"   Command: {python_exe}")
        print(f"   Args: [\"{MCP_SERVER_PATH}\"]")

    except Exception as e:
        handle_cli_error(e, "Testing MCP server", getattr(args, 'verbose', False))


def handle_mcp_list_tools_command(args):
    """List available MCP tools"""
    try:
        print_header("🔧 Available MCP Tools")

        # Core workflow tools
        print("\n📋 Core Workflow Tools:")
        core_tools = [
            ("submit_preflight_assessment", "Baseline epistemic assessment (PREFLIGHT)"),
            ("submit_check_assessment", "Decision point validation (CHECK)"),
            ("submit_postflight_assessment", "Final assessment + calculate deltas (POSTFLIGHT)"),
        ]
        for name, desc in core_tools:
            print(f"   • {name:35s} - {desc}")

        # Session management
        print("\n🔄 Session Management:")
        session_tools = [
            ("session_create", "Initialize new session"),
            ("resume_previous_session", "Load previous context"),
            ("get_epistemic_state", "Query current vectors"),
            ("get_session_summary", "Full session history"),
            ("get_calibration_report", "Check calibration accuracy"),
        ]
        for name, desc in session_tools:
            print(f"   • {name:35s} - {desc}")

        # Goal Management (NEW)
        print("\n🎯 Goal Management:")
        goal_tools = [
            ("create_goal", "Create structured goal with ScopeVector"),
            ("add_task", "Add task to existing goal"),
            ("complete_task", "Mark task as complete"),
            ("get_goal_progress", "Check goal completion progress"),
            ("list_goals", "List all goals for session"),
        ]
        for name, desc in goal_tools:
            print(f"   • {name:35s} - {desc}")

        # Cross-AI Coordination (NEW)
        print("\n🤝 Cross-AI Coordination:")
        coordination_tools = [
            ("discover_goals", "Find goals from other AIs"),
            ("resume_goal", "Resume another AI's goal"),
        ]
        for name, desc in coordination_tools:
            print(f"   • {name:35s} - {desc}")

        # Checkpoints (NEW)
        print("\n💾 Checkpoints:")
        checkpoint_tools = [
            ("create_git_checkpoint", "Save state to git notes"),
            ("load_git_checkpoint", "Restore state from git notes"),
        ]
        for name, desc in checkpoint_tools:
            print(f"   • {name:35s} - {desc}")

        # Handoff Reports (NEW)
        print("\n📝 Handoff Reports:")
        handoff_tools = [
            ("create_handoff_report", "Create session handoff report"),
            ("query_handoff_reports", "Query past handoff reports"),
        ]
        for name, desc in handoff_tools:
            print(f"   • {name:35s} - {desc}")

        # Guidance
        print("\n📖 Guidance:")
        guidance_tools = [
            ("get_empirica_introduction", "Framework introduction"),
            ("get_workflow_guidance", "Workflow step guidance"),
            ("cli_help", "CLI command help"),
        ]
        for name, desc in guidance_tools:
            print(f"   • {name:35s} - {desc}")

        total = (len(core_tools) + len(session_tools) + len(goal_tools) +
                 len(coordination_tools) + len(checkpoint_tools) +
                 len(handoff_tools) + len(guidance_tools))

        print(f"\n📊 Total tools: {total}")

        if args.verbose:
            print("\n💡 Use 'empirica mcp call <tool_name>' to test a tool")
            print("💡 See docs/human/developers/MCP_SERVER_REFERENCE.md for detailed documentation")

    except Exception as e:
        handle_cli_error(e, "Listing MCP tools", getattr(args, 'verbose', False))


def handle_mcp_call_command(args):
    """Call MCP tool directly (for testing)"""
    try:
        print_header(f"🔧 Calling MCP Tool: {args.tool_name}")

        # Parse arguments
        tool_args = {}
        if args.arguments:
            try:
                tool_args = json.loads(args.arguments)
            except json.JSONDecodeError:
                print("❌ Invalid JSON arguments")
                print("💡 Example: empirica mcp call cli_help '{\"command\": \"bootstrap\"}'")
                return

        # Import MCP client and call tool
        # For now, provide instructions
        print("⏳ Direct MCP tool calling from CLI is experimental")
        print(f"\n📝 Tool: {args.tool_name}")
        print(f"📝 Arguments: {json.dumps(tool_args, indent=2)}")
        print("\n💡 To use this tool, configure it in your IDE's MCP client")
        print("💡 See docs/human/developers/MCP_SERVER_REFERENCE.md")

    except Exception as e:
        handle_cli_error(e, "Calling MCP tool", getattr(args, 'verbose', False))


# Helper functions
def _is_mcp_running():
    """Check if MCP server is running"""
    if not MCP_PID_FILE.exists():
        return False

    try:
        pid = _get_mcp_pid()
        os.kill(pid, 0)  # Signal 0 just checks if process exists
        return True
    except (ProcessLookupError, ValueError, OSError):
        # Process doesn't exist, clean up stale PID file
        MCP_PID_FILE.unlink(missing_ok=True)
        return False


def _get_mcp_pid():
    """Get MCP server PID"""
    with open(MCP_PID_FILE) as f:
        return int(f.read().strip())
