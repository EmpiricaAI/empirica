"""
Edit Verification Command - Metacognitive Edit Guard for Reliable File Modifications

Implements the Empirica Edit Guard system as a CLI command.
Prevents 80% of AI edit failures by assessing epistemic confidence BEFORE attempting edits.
"""

import json
import logging
import re
from pathlib import Path

from ..cli_utils import handle_cli_error

logger = logging.getLogger(__name__)


def execute_edit_strategy_sync(strategy, file_path, old_str, new_str):
    """
    Execute edit strategy synchronously (sync version of async methods).

    Args:
        strategy: "atomic_edit" | "bash_fallback" | "re_read_first"
        file_path: Path to file
        old_str: String to replace
        new_str: Replacement string

    Returns:
        dict with success, message, changes_made keys
    """
    if strategy == "atomic_edit":
        return atomic_edit_sync(file_path, old_str, new_str)
    elif strategy == "bash_fallback":
        return bash_line_replacement_sync(file_path, old_str, new_str)
    elif strategy == "re_read_first":
        return re_read_then_edit_sync(file_path, old_str, new_str)
    else:
        return {
            "success": False,
            "strategy_used": "unknown",
            "message": f"Unknown strategy: {strategy}",
            "changes_made": False,
        }


def atomic_edit_sync(file_path, old_str, new_str):
    """Synchronous version of atomic_edit method."""
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()

        # Check if pattern exists
        if old_str not in content:
            return {
                "success": False,
                "strategy_used": "atomic_edit",
                "message": f"Pattern not found in {file_path}",
                "changes_made": False,
            }

        # Count occurrences
        count = content.count(old_str)
        if count > 1:
            return {
                "success": False,
                "strategy_used": "atomic_edit",
                "message": f"Ambiguous: found {count} matches (expected 1)",
                "changes_made": False,
            }

        # Perform replacement
        new_content = content.replace(old_str, new_str, 1)

        # Write back
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return {
            "success": True,
            "strategy_used": "atomic_edit",
            "message": f"Successfully replaced 1 occurrence in {file_path}",
            "changes_made": True,
        }

    except Exception as e:
        return {"success": False, "strategy_used": "atomic_edit", "message": f"Error: {e!s}", "changes_made": False}


def bash_line_replacement_sync(file_path, old_str, new_str):
    """Synchronous version of bash_line_replacement method."""
    try:
        with open(file_path, encoding="utf-8") as f:
            lines = f.readlines()

        # Try exact match first
        content = "".join(lines)
        if old_str in content:
            # Exact match exists - use it
            new_content = content.replace(old_str, new_str, 1)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            return {
                "success": True,
                "strategy_used": "bash_fallback",
                "message": "Successfully replaced using exact match approach",
                "changes_made": True,
            }
        else:
            # No exact match - try regex with flexible whitespace
            pattern = make_flexible_pattern(old_str)
            found = False
            new_lines = []

            for line in lines:
                if re.search(pattern, line):
                    # Replace this line
                    new_line = re.sub(pattern, new_str, line)
                    new_lines.append(new_line)
                    found = True
                else:
                    new_lines.append(line)

            if not found:
                return {
                    "success": False,
                    "strategy_used": "bash_fallback",
                    "message": "Pattern not found (even with flexible whitespace)",
                    "changes_made": False,
                }

            # Write back
            with open(file_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

            return {
                "success": True,
                "strategy_used": "bash_fallback",
                "message": "Successfully replaced using flexible pattern matching",
                "changes_made": True,
            }

    except Exception as e:
        return {"success": False, "strategy_used": "bash_fallback", "message": f"Error: {e!s}", "changes_made": False}


def re_read_then_edit_sync(file_path, old_str, new_str):
    """Synchronous version of re_read_then_edit method."""
    try:
        # Read current file content
        with open(file_path, encoding="utf-8") as f:
            content = f.read()

        # Verify the pattern exists
        if old_str not in content:
            return {
                "success": False,
                "strategy_used": "re_read_first",
                "message": "After re-reading: pattern still not found",
                "changes_made": False,
            }

        # Pattern exists - try atomic edit
        return atomic_edit_sync(file_path, old_str, new_str)

    except Exception as e:
        return {"success": False, "strategy_used": "re_read_first", "message": f"Error: {e!s}", "changes_made": False}


def make_flexible_pattern(old_str):
    """Convert exact string to regex with flexible whitespace."""
    # Escape special regex characters
    import re

    pattern = re.escape(old_str)

    # Replace literal spaces with \s+ (flexible whitespace)
    pattern = pattern.replace(r"\ ", r"\s+")

    return pattern


def handle_edit_with_confidence_command(args):
    """Handle edit-with-confidence command - Assess confidence before editing"""
    try:
        from empirica.components.edit_verification.confidence_assessor import EditConfidenceAssessor

        # Parse arguments
        file_path = args.file_path
        old_str = args.old_str
        new_str = args.new_str
        context_source = getattr(args, "context_source", "memory")
        output_format = getattr(args, "output", "json")

        # Validate required arguments
        if not file_path or old_str is None or new_str is None:
            result = {
                "ok": False,
                "error": "Missing required arguments: --file-path, --old-str, --new-str",
                "received": {
                    "file_path": bool(file_path),
                    "old_str": old_str is not None,
                    "new_str": new_str is not None,
                },
            }
            if output_format == "json":
                print(json.dumps(result, indent=2))
            else:
                print("❌ Missing required arguments")
            return 1

        # Validate file exists
        if not Path(file_path).exists():
            result = {"ok": False, "error": f"File does not exist: {file_path}"}
            if output_format == "json":
                print(json.dumps(result, indent=2))
            else:
                print(f"❌ File does not exist: {file_path}")
            return 1

        # Initialize components
        assessor = EditConfidenceAssessor()

        # Assess epistemic confidence
        assessment = assessor.assess(file_path=file_path, old_str=old_str, context_source=context_source)

        # Get recommended strategy
        strategy, reasoning = assessor.recommend_strategy(assessment)

        # Execute with chosen strategy using sync implementations
        result = execute_edit_strategy_sync(strategy, file_path, old_str, new_str)

        # Check if result has the expected structure
        if isinstance(result, dict) and "success" in result:
            # Already in correct format
            pass
        else:
            # Something went wrong, create error result
            result = {
                "success": False,
                "strategy_used": strategy,
                "message": f"Edit operation failed: {result if result else 'Unknown error'}",
                "changes_made": False,
            }

        # Format output
        output_result = {
            "ok": result.get("success", False),
            "strategy": strategy,
            "reasoning": reasoning,
            "confidence": assessment["overall"],
            "result": result.get("message", ""),
            "changes_made": result.get("changes_made", False),
            "file_path": file_path,
            "assessment_details": {
                "context_freshness": assessment["context"],
                "whitespace_uncertainty": assessment["uncertainty"],
                "pattern_signal": assessment["signal"],
                "truncation_clarity": assessment["clarity"],
            },
        }

        if output_format == "json":
            print(json.dumps(output_result, indent=2))
        else:
            status = "✅" if output_result["ok"] else "❌"
            print(f"{status} Edit operation: {output_result['result']}")
            print(f"   Strategy: {strategy} (confidence: {assessment['overall']:.2f})")
            print(f"   Changes: {'Yes' if output_result['changes_made'] else 'No'}")
            print(f"   File: {file_path}")

        return None  # Avoid duplicate output and exit code issues

    except Exception as e:
        handle_cli_error(e, "Edit with confidence", getattr(args, "verbose", False))
        return None
