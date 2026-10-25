"""
Transcript Parser — Parse Claude Code and Claude.ai transcripts for epistemic mining.

Reads .jsonl session transcripts from Claude Code (.claude/projects/{path}/*.jsonl)
and Claude.ai exports, producing structured ConversationTurn objects suitable for
artifact extraction.

Record types handled:
- user: User messages (prompts, corrections, feedback)
- assistant: AI responses (text, tool_use, thinking)
- progress: Hook execution events (filtered out — operational noise)
- system: Compact boundaries, hook summaries, errors
- file-history-snapshot: File change tracking (useful for change context)
- summary: Conversation summaries (post-compaction)

Architecture:
    .jsonl file ──> TranscriptParser.parse_session()
                         ├── iter_conversation_turns() ──> ConversationTurn[]
                         ├── extract_tool_chains() ──> ToolChain[]
                         └── session_metadata() ──> SessionMetadata

    sessions-index.json ──> SessionIndex.discover_sessions()
"""

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


# --- Data Classes ---


class RecordType(str, Enum):
    """Claude Code transcript record types."""

    USER = "user"
    ASSISTANT = "assistant"
    PROGRESS = "progress"
    SYSTEM = "system"
    SUMMARY = "summary"
    FILE_HISTORY = "file-history-snapshot"
    UNKNOWN = "unknown"


class ContentBlockType(str, Enum):
    """Types of content blocks within assistant messages."""

    TEXT = "text"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"
    IMAGE = "image"


@dataclass
class ContentBlock:
    """A single content block from a message."""

    block_type: ContentBlockType
    text: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_use_id: str = ""
    thinking: str = ""


@dataclass
class TranscriptRecord:
    """A single record from a .jsonl transcript."""

    uuid: str
    timestamp: str
    record_type: RecordType
    parent_uuid: str | None = None
    session_id: str = ""
    git_branch: str = ""
    is_sidechain: bool = False
    agent_id: str = ""
    cwd: str = ""

    # For user/assistant records
    role: str = ""
    content_blocks: list[ContentBlock] = field(default_factory=list)
    raw_content: str = ""  # For simple string content (user messages)
    model: str = ""

    # Token usage (assistant only)
    input_tokens: int = 0
    output_tokens: int = 0

    # For system records
    subtype: str = ""
    system_content: str = ""
    level: str = ""

    # Raw data for anything we don't parse
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolChain:
    """A tool invocation paired with its result."""

    tool_name: str
    tool_input: dict[str, Any]
    tool_use_id: str
    result_content: str = ""
    timestamp: str = ""
    success: bool = True  # Inferred from result content


@dataclass
class ConversationTurn:
    """A user message and the assistant's response (may include tool chains)."""

    turn_index: int
    user_message: str
    assistant_text: str = ""
    thinking: str = ""
    tool_chains: list[ToolChain] = field(default_factory=list)
    timestamp: str = ""
    model: str = ""
    is_sidechain: bool = False
    git_branch: str = ""

    # Compact boundaries within this turn
    compact_occurred: bool = False
    summary_text: str = ""


@dataclass
class SessionMetadata:
    """Metadata for a session from sessions-index.json."""

    session_id: str
    full_path: str
    first_prompt: str = ""
    summary: str = ""
    message_count: int = 0
    created: str = ""
    modified: str = ""
    git_branch: str = ""
    project_path: str = ""
    is_sidechain: bool = False
    pr_number: int | None = None
    pr_url: str = ""


# --- Session Index ---


class SessionIndex:
    """Reads sessions-index.json to discover available sessions."""

    def __init__(self, claude_dir: str | None = None):
        self.claude_dir = Path(claude_dir or Path.home() / ".claude")

    def discover_projects(self) -> list[str]:
        """List all project directories under .claude/projects/."""
        projects_dir = self.claude_dir / "projects"
        if not projects_dir.exists():
            return []
        return [d.name for d in sorted(projects_dir.iterdir()) if d.is_dir() and not d.name.startswith(".")]

    def get_sessions(self, project_name: str) -> list[SessionMetadata]:
        """Read sessions-index.json for a project.

        Falls back to direct .jsonl file discovery if index doesn't exist.
        """
        project_dir = self.claude_dir / "projects" / project_name
        index_path = project_dir / "sessions-index.json"

        if index_path.exists():
            try:
                data = json.loads(index_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to read sessions index for {project_name}: {e}")
                data = {"entries": []}

            sessions = []
            for entry in data.get("entries", []):
                sessions.append(
                    SessionMetadata(
                        session_id=entry.get("sessionId", ""),
                        full_path=entry.get("fullPath", ""),
                        first_prompt=entry.get("firstPrompt", ""),
                        summary=entry.get("summary", ""),
                        message_count=entry.get("messageCount", 0),
                        created=entry.get("created", ""),
                        modified=entry.get("modified", ""),
                        git_branch=entry.get("gitBranch", ""),
                        project_path=entry.get("projectPath", ""),
                        is_sidechain=entry.get("isSidechain", False),
                        pr_number=entry.get("prNumber"),
                        pr_url=entry.get("prUrl", ""),
                    )
                )
            return sessions

        # Fallback: discover .jsonl files directly
        if not project_dir.exists():
            return []

        sessions = []
        for jsonl_file in sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            session_id = jsonl_file.stem
            stat = jsonl_file.stat()
            sessions.append(
                SessionMetadata(
                    session_id=session_id,
                    full_path=str(jsonl_file),
                    message_count=1,  # Unknown without reading file
                    modified=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    project_path=str(project_dir),
                )
            )
        return sessions

    def get_all_sessions(self, min_messages: int = 2) -> list[SessionMetadata]:
        """Get sessions across all projects, filtered by minimum message count."""
        all_sessions = []
        for project in self.discover_projects():
            sessions = self.get_sessions(project)
            all_sessions.extend(s for s in sessions if s.message_count >= min_messages and not s.is_sidechain)
        return sorted(all_sessions, key=lambda s: s.modified, reverse=True)


# --- Transcript Parser ---


class TranscriptParser:
    """Parse Claude Code .jsonl transcripts into structured records."""

    # Record types that carry epistemic signal (vs operational noise)
    SIGNAL_TYPES: ClassVar[set[RecordType]] = {
        RecordType.USER,
        RecordType.ASSISTANT,
        RecordType.SYSTEM,
        RecordType.SUMMARY,
    }

    def parse_session(self, jsonl_path: str) -> list[TranscriptRecord]:
        """Parse all records from a .jsonl transcript file.

        Args:
            jsonl_path: Path to the .jsonl transcript file.

        Returns:
            List of parsed TranscriptRecord objects, ordered by timestamp.
        """
        path = Path(jsonl_path)
        if not path.exists():
            logger.warning(f"Transcript file not found: {jsonl_path}")
            return []

        records = []
        line_num = 0
        for line in path.open("r", encoding="utf-8"):
            line_num += 1
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                record = self._parse_record(raw)
                if record:
                    records.append(record)
            except json.JSONDecodeError:
                logger.debug(f"Skipping invalid JSON at line {line_num} in {path.name}")
            except Exception as e:
                logger.debug(f"Error parsing line {line_num} in {path.name}: {e}")

        return sorted(records, key=lambda r: r.timestamp)

    def _parse_record(self, raw: dict[str, Any]) -> TranscriptRecord | None:
        """Parse a single JSON record into a TranscriptRecord."""
        raw_type = raw.get("type", "unknown")
        try:
            record_type = RecordType(raw_type)
        except ValueError:
            record_type = RecordType.UNKNOWN

        # Skip progress records — they're operational noise
        if record_type == RecordType.PROGRESS:
            return None

        record = TranscriptRecord(
            uuid=raw.get("uuid", ""),
            timestamp=raw.get("timestamp", ""),
            record_type=record_type,
            parent_uuid=raw.get("parentUuid"),
            session_id=raw.get("sessionId", ""),
            git_branch=raw.get("gitBranch", ""),
            is_sidechain=raw.get("isSidechain", False),
            agent_id=raw.get("agentId", ""),
            cwd=raw.get("cwd", ""),
            raw=raw,
        )

        if record_type == RecordType.USER:
            self._parse_user_record(record, raw)
        elif record_type == RecordType.ASSISTANT:
            self._parse_assistant_record(record, raw)
        elif record_type == RecordType.SYSTEM:
            self._parse_system_record(record, raw)
        elif record_type == RecordType.SUMMARY:
            self._parse_summary_record(record, raw)

        return record

    def _parse_user_record(self, record: TranscriptRecord, raw: dict[str, Any]):
        """Parse user message content."""
        record.role = "user"
        message = raw.get("message", {})
        content = message.get("content", "") if isinstance(message, dict) else ""

        if isinstance(content, str):
            record.raw_content = content
        elif isinstance(content, list):
            # User messages with tool results
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    if block.get("type") == "tool_result":
                        result_content = block.get("content", "")
                        if isinstance(result_content, list):
                            result_content = " ".join(b.get("text", "") for b in result_content if isinstance(b, dict))
                        record.content_blocks.append(
                            ContentBlock(
                                block_type=ContentBlockType.TOOL_RESULT,
                                tool_use_id=block.get("tool_use_id", ""),
                                text=str(result_content)[:5000],  # Truncate large results
                            )
                        )
                    elif block.get("type") == "text":
                        parts.append(block.get("text", ""))
            record.raw_content = "\n".join(parts)

    def _parse_assistant_record(self, record: TranscriptRecord, raw: dict[str, Any]):
        """Parse assistant message content blocks."""
        record.role = "assistant"
        message = raw.get("message", {})
        if not isinstance(message, dict):
            return

        record.model = message.get("model", "")

        usage = message.get("usage", {})
        if isinstance(usage, dict):
            record.input_tokens = usage.get("input_tokens", 0)
            record.output_tokens = usage.get("output_tokens", 0)

        for block in message.get("content", []):
            if not isinstance(block, dict):
                continue

            block_type_str = block.get("type", "")

            if block_type_str == "text":
                record.content_blocks.append(
                    ContentBlock(
                        block_type=ContentBlockType.TEXT,
                        text=block.get("text", ""),
                    )
                )
            elif block_type_str == "tool_use":
                record.content_blocks.append(
                    ContentBlock(
                        block_type=ContentBlockType.TOOL_USE,
                        tool_name=block.get("name", ""),
                        tool_input=block.get("input", {}),
                        tool_use_id=block.get("id", ""),
                    )
                )
            elif block_type_str == "thinking":
                record.content_blocks.append(
                    ContentBlock(
                        block_type=ContentBlockType.THINKING,
                        thinking=block.get("thinking", ""),
                    )
                )

    def _parse_system_record(self, record: TranscriptRecord, raw: dict[str, Any]):
        """Parse system record."""
        record.subtype = raw.get("subtype", "")
        record.system_content = raw.get("content", "")
        record.level = raw.get("level", "")

    def _parse_summary_record(self, record: TranscriptRecord, raw: dict[str, Any]):
        """Parse summary record (post-compaction)."""
        record.role = "summary"
        message = raw.get("message", {})
        if isinstance(message, dict):
            content = message.get("content", "")
            if isinstance(content, str):
                record.raw_content = content
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                record.raw_content = "\n".join(parts)

    def iter_conversation_turns(
        self, records: list[TranscriptRecord], include_sidechains: bool = False
    ) -> Iterator[ConversationTurn]:
        """Thread records into conversation turns (user → assistant pairs).

        Args:
            records: Parsed transcript records (should be sorted by timestamp).
            include_sidechains: Whether to include subagent conversations.

        Yields:
            ConversationTurn objects representing complete user→assistant exchanges.
        """
        # Filter to signal-bearing records
        filtered = [
            r for r in records if r.record_type in self.SIGNAL_TYPES and (include_sidechains or not r.is_sidechain)
        ]

        turn_index = 0
        current_user_msg = ""
        current_user_ts = ""
        current_assistant_text_parts: list[str] = []
        current_thinking_parts: list[str] = []
        current_tool_uses: dict[str, ContentBlock] = {}  # tool_use_id -> block
        current_model = ""
        current_branch = ""
        compact_in_turn = False
        summary_in_turn = ""

        for record in filtered:
            if record.record_type == RecordType.USER:
                # A tool result arrives as a user record whose content is only tool_result blocks, so its raw_content is empty. It
                # continues the turn in progress: treating it as a new turn replaced the user message with "" and dropped every
                # assistant text, thinking and tool call after the first tool call of the turn.
                if not record.raw_content.strip() and any(
                    b.block_type == ContentBlockType.TOOL_RESULT for b in record.content_blocks
                ):
                    continue
                # Yield previous turn if we have one
                if current_user_msg:
                    tool_chains = self._resolve_tool_chains(current_tool_uses, filtered)
                    yield ConversationTurn(
                        turn_index=turn_index,
                        user_message=current_user_msg,
                        assistant_text="\n".join(current_assistant_text_parts),
                        thinking="\n".join(current_thinking_parts),
                        tool_chains=tool_chains,
                        timestamp=current_user_ts,
                        model=current_model,
                        git_branch=current_branch,
                        compact_occurred=compact_in_turn,
                        summary_text=summary_in_turn,
                    )
                    turn_index += 1

                # Start new turn
                current_user_msg = record.raw_content
                current_user_ts = record.timestamp
                current_assistant_text_parts = []
                current_thinking_parts = []
                current_tool_uses = {}
                current_model = ""
                current_branch = record.git_branch
                compact_in_turn = False
                summary_in_turn = ""

            elif record.record_type == RecordType.ASSISTANT:
                current_model = record.model or current_model
                for block in record.content_blocks:
                    if block.block_type == ContentBlockType.TEXT:
                        current_assistant_text_parts.append(block.text)
                    elif block.block_type == ContentBlockType.THINKING:
                        current_thinking_parts.append(block.thinking)
                    elif block.block_type == ContentBlockType.TOOL_USE:
                        current_tool_uses[block.tool_use_id] = block

            elif record.record_type == RecordType.SYSTEM:
                if record.subtype == "compact_boundary":
                    compact_in_turn = True

            elif record.record_type == RecordType.SUMMARY:
                summary_in_turn = record.raw_content

        # Yield final turn
        if current_user_msg:
            tool_chains = self._resolve_tool_chains(current_tool_uses, filtered)
            yield ConversationTurn(
                turn_index=turn_index,
                user_message=current_user_msg,
                assistant_text="\n".join(current_assistant_text_parts),
                thinking="\n".join(current_thinking_parts),
                tool_chains=tool_chains,
                timestamp=current_user_ts,
                model=current_model,
                git_branch=current_branch,
                compact_occurred=compact_in_turn,
                summary_text=summary_in_turn,
            )

    def _resolve_tool_chains(
        self,
        tool_uses: dict[str, ContentBlock],
        all_records: list[TranscriptRecord],
    ) -> list[ToolChain]:
        """Match tool_use blocks with their tool_result responses."""
        chains = []
        # Build a lookup of tool results from user records
        result_lookup: dict[str, str] = {}
        for record in all_records:
            if record.record_type == RecordType.USER:
                for block in record.content_blocks:
                    if block.block_type == ContentBlockType.TOOL_RESULT:
                        result_lookup[block.tool_use_id] = block.text

        for tool_use_id, block in tool_uses.items():
            result_text = result_lookup.get(tool_use_id, "")
            # Infer success from result content
            success = (
                not any(err in result_text.lower() for err in ["error", "failed", "exception", "traceback"])
                if result_text
                else True
            )

            chains.append(
                ToolChain(
                    tool_name=block.tool_name,
                    tool_input=block.tool_input,
                    tool_use_id=tool_use_id,
                    result_content=result_text,
                    success=success,
                )
            )

        return chains

    def extract_tool_chains(self, records: list[TranscriptRecord]) -> list[ToolChain]:
        """Extract all tool chains from a session (flat list)."""
        all_chains = []
        for turn in self.iter_conversation_turns(records):
            all_chains.extend(turn.tool_chains)
        return all_chains

    def session_stats(self, records: list[TranscriptRecord]) -> dict[str, Any]:
        """Compute statistics for a session's records."""
        user_count = sum(1 for r in records if r.record_type == RecordType.USER)
        assistant_count = sum(1 for r in records if r.record_type == RecordType.ASSISTANT)
        total_output_tokens = sum(r.output_tokens for r in records if r.record_type == RecordType.ASSISTANT)

        tools_used: dict[str, int] = {}
        for r in records:
            if r.record_type == RecordType.ASSISTANT:
                for block in r.content_blocks:
                    if block.block_type == ContentBlockType.TOOL_USE:
                        tools_used[block.tool_name] = tools_used.get(block.tool_name, 0) + 1

        compactions = sum(1 for r in records if r.record_type == RecordType.SYSTEM and r.subtype == "compact_boundary")

        models = {r.model for r in records if r.record_type == RecordType.ASSISTANT and r.model}

        timestamps = [r.timestamp for r in records if r.timestamp]
        duration_minutes = 0.0
        if len(timestamps) >= 2:
            try:
                start = datetime.fromisoformat(timestamps[0].replace("Z", "+00:00"))
                end = datetime.fromisoformat(timestamps[-1].replace("Z", "+00:00"))
                duration_minutes = (end - start).total_seconds() / 60
            except (ValueError, TypeError):
                pass

        return {
            "user_messages": user_count,
            "assistant_messages": assistant_count,
            "total_output_tokens": total_output_tokens,
            "tools_used": tools_used,
            "compactions": compactions,
            "models": list(models),
            "duration_minutes": round(duration_minutes, 1),
            "record_count": len(records),
        }


# --- Claude.ai Export Parser ---


def _extract_content_block_parts(content: list) -> list[str]:
    """Extract text parts from a list of content blocks."""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            block_type = block.get("type", "")
            if block_type == "text":
                parts.append(block.get("text", ""))
            elif block_type == "tool_use":
                _extract_tool_use_text(block, parts)
            elif block_type == "tool_result":
                _extract_tool_result_text(block, parts)
    return parts


def _extract_tool_use_text(block: dict, parts: list[str]) -> None:
    """Extract text from a tool_use content block."""
    tool_name = block.get("name", "unknown")
    tool_input = block.get("input", {})
    msg_text = block.get("message", "")
    if msg_text:
        parts.append(f"[Tool: {tool_name}] {msg_text}")
    elif isinstance(tool_input, dict):
        for key in ("query", "command", "description"):
            if key in tool_input:
                parts.append(f"[Tool: {tool_name}] {tool_input[key]}")
                break


def _extract_tool_result_text(block: dict, parts: list[str]) -> None:
    """Extract text from a tool_result content block."""
    inner = block.get("content", [])
    if isinstance(inner, list):
        for inner_block in inner:
            if isinstance(inner_block, dict) and inner_block.get("type") == "text":
                text = inner_block.get("text", "")
                if text and len(text) < 500:
                    parts.append(text)


class ClaudeAIParser:
    """Parse Claude.ai conversation exports.

    Claude.ai exports are ZIP archives containing:
    - conversations.json: Array of conversations with chat_messages[]
    - memories.json: Claude's memory about the user (global + per-project)
    - projects.json: Project metadata with docs
    - users.json: User profile

    Messages have content[] blocks (text, tool_use, tool_result).
    IMPORTANT: Always parse content[] as canonical source — the `text` field
    is a display-oriented flattening that drops tool blocks and has mismatches
    in ~13% of messages.

    This parser normalizes Claude.ai format into the same ConversationTurn
    interface used by TranscriptParser.
    """

    def parse_export(self, file_path: str) -> tuple[list[ConversationTurn], dict[str, Any]]:
        """Parse a Claude.ai export (ZIP archive or JSON file).

        Args:
            file_path: Path to the exported ZIP or conversations.json file.

        Returns:
            Tuple of (conversation turns, export metadata).
        """
        path = Path(file_path)
        if not path.exists():
            logger.warning(f"Export file not found: {file_path}")
            return [], {}

        # Handle ZIP archives
        conversations_data = None
        memories_data = None
        projects_data = None
        if path.suffix == ".zip":
            conversations_data, memories_data, projects_data = self._extract_zip(path)
        else:
            try:
                conversations_data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to read export file: {e}")
                return [], {}

        if conversations_data is None:
            return [], {}

        # Normalize to list of conversations
        conversations = []
        if isinstance(conversations_data, list):
            conversations = conversations_data
        elif isinstance(conversations_data, dict):
            if "conversations" in conversations_data:
                conversations = conversations_data["conversations"]
            elif "chat_messages" in conversations_data:
                conversations = [conversations_data]
            else:
                conversations = [conversations_data]

        all_turns = []
        metadata = {
            "source": "claude.ai",
            "conversation_count": len(conversations),
            "file_path": str(path),
        }

        # Add memories and projects to metadata if available
        if memories_data:
            metadata["has_memories"] = True
            metadata["memories"] = memories_data
        if projects_data:
            metadata["has_projects"] = True
            metadata["project_count"] = len(projects_data) if isinstance(projects_data, list) else 0

        for conv in conversations:
            turns = self._parse_conversation(conv)
            all_turns.extend(turns)

        metadata["total_turns"] = len(all_turns)
        return all_turns, metadata

    def _extract_zip(self, zip_path: Path) -> tuple[Any | None, Any | None, Any | None]:
        """Extract conversations, memories, and projects from a ZIP export."""
        import zipfile

        conversations = None
        memories = None
        projects = None

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    basename = Path(name).name
                    try:
                        raw = zf.read(name).decode("utf-8")
                        data = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue

                    if basename == "conversations.json":
                        conversations = data
                    elif basename == "memories.json":
                        memories = data
                    elif basename == "projects.json":
                        projects = data
        except zipfile.BadZipFile:
            logger.warning(f"Invalid ZIP file: {zip_path}")
        except Exception as e:
            logger.warning(f"Failed to extract ZIP: {e}")

        return conversations, memories, projects

    def _parse_conversation(self, conv: dict[str, Any]) -> list[ConversationTurn]:
        """Parse a single conversation from Claude.ai export."""
        turns = []

        messages = conv.get("chat_messages") or conv.get("messages") or conv.get("content", [])
        if not isinstance(messages, list):
            return turns

        turn_index = 0
        current_user_msg = ""
        current_user_ts = ""

        for msg in messages:
            if not isinstance(msg, dict):
                continue

            sender = msg.get("sender", msg.get("role", ""))
            content = self._extract_message_content(msg)
            timestamp = msg.get("created_at", msg.get("timestamp", ""))
            tool_chains = self._extract_tool_chains(msg)

            if sender in ("human", "user"):
                current_user_msg = content
                current_user_ts = timestamp

            elif sender == "assistant" and current_user_msg:
                turns.append(
                    ConversationTurn(
                        turn_index=turn_index,
                        user_message=current_user_msg,
                        assistant_text=content,
                        timestamp=current_user_ts,
                        model=msg.get("model", ""),
                        tool_chains=tool_chains,
                    )
                )
                turn_index += 1
                current_user_msg = ""

        return turns

    def _extract_message_content(self, msg: dict[str, Any]) -> str:
        """Extract text content from a message using content[] as canonical source."""
        content = msg.get("content")
        if isinstance(content, list):
            parts = _extract_content_block_parts(content)
            if parts:
                return "\n".join(parts)

        # Fallback to text field
        if "text" in msg and isinstance(msg["text"], str):
            return msg["text"]

        if isinstance(content, str):
            return content

        return ""

    def _extract_tool_chains(self, msg: dict[str, Any]) -> list[ToolChain]:
        """Extract tool chains from a Claude.ai message's content blocks."""
        content = msg.get("content")
        if not isinstance(content, list):
            return []

        # Build lookup of tool_use blocks by id
        tool_uses: dict[str, dict[str, Any]] = {}
        tool_results: dict[str, dict[str, Any]] = {}

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tool_uses[block.get("id", "")] = block
            elif block.get("type") == "tool_result":
                tool_results[block.get("tool_use_id", "")] = block

        chains = []
        for tool_id, use_block in tool_uses.items():
            result_block = tool_results.get(tool_id)
            result_text = ""
            is_error = False
            if result_block:
                is_error = result_block.get("is_error", False)
                inner = result_block.get("content", [])
                if isinstance(inner, list):
                    texts = []
                    for b in inner:
                        if isinstance(b, dict) and b.get("type") == "text":
                            texts.append(b.get("text", ""))
                    result_text = "\n".join(texts)

            chains.append(
                ToolChain(
                    tool_name=use_block.get("name", "unknown"),
                    tool_input=use_block.get("input", {}),
                    tool_use_id=tool_id,
                    result_content=result_text[:1000] if result_text else "",
                    success=not is_error,
                )
            )

        return chains
