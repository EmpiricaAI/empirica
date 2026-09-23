# ClaudeAIParser — Claude.ai conversation import

`ClaudeAIParser` reads a Claude.ai export and normalises it into the same
`ConversationTurn` objects `TranscriptParser` produces for Claude Code
transcripts, so one artifact extractor serves both sources.

It lives in `empirica/core/canonical/transcript_parser.py`, beside
`TranscriptParser`, because they share that interface.

> Rewritten 2026-09-23 against the code. The previous revision documented a
> module path that does not exist (`empirica.core.profile.claude_ai_parser`) and
> three methods that were never on the class (`parse_zip`, `parse_json`,
> `extract_artifacts`). Everything below was read off the implementation and the
> one caller.

## Export format

A Claude.ai export is a ZIP archive holding:

- `conversations.json` — the conversations, each with `chat_messages[]`
- `memories.json` — Claude's memory about the user
- `projects.json` — project metadata and docs
- `users.json` — the user profile

A conversation's messages carry `content[]` blocks:

```json
[
  {
    "uuid": "abc123",
    "name": "Conversation Title",
    "created_at": "2026-01-15T10:30:00Z",
    "chat_messages": [
      {
        "uuid": "msg-1",
        "sender": "human",
        "content": [{ "type": "text", "text": "..." }],
        "created_at": "2026-01-15T10:30:00Z"
      },
      {
        "uuid": "msg-2",
        "sender": "assistant",
        "content": [
          { "type": "text", "text": "..." },
          { "type": "tool_use", "name": "search", "input": {} },
          { "type": "tool_result", "content": "..." }
        ]
      }
    ]
  }
]
```

**Parse `content[]`, never the `text` field.** `text` is a display-oriented
flattening that drops tool blocks, and the two disagree on about 13% of messages
(87 of 654 when this was measured). The parser's own docstring carries the same
warning.

## Usage

```python
from empirica.core.canonical.artifact_extractor import ArtifactExtractor
from empirica.core.canonical.transcript_parser import ClaudeAIParser

parser = ClaudeAIParser()

# One entry point. It takes the ZIP, or a conversations.json directly.
turns, metadata = parser.parse_export("/path/to/claude-export.zip")

extractor = ArtifactExtractor(min_confidence=0.3)
result = extractor.extract_all(turns, source="claude-ai")
```

`parse_export` returns `(turns, metadata)`:

- `turns` — `list[ConversationTurn]`, flattened across every conversation in the
  export.
- `metadata` — `source`, `conversation_count`, `file_path`, `total_turns`, plus
  `has_memories` / `memories` and `has_projects` / `project_count` when the ZIP
  carried them.

A missing file, unreadable JSON or an unrecognised shape returns `([], {})` with
a warning logged. It does not raise.

## Content block types

| Type | Description |
|------|-------------|
| `text` | plain message text |
| `tool_use` | a tool invocation, with name and input |
| `tool_result` | the result of one |
| `thinking` | reasoning, when the export carries it |

## Through the CLI

```bash
empirica profile-import --source claude-ai --file ~/Downloads/claude-export.zip
empirica profile-import --source claude-ai --file ~/Downloads/claude-export.zip --dry-run
```

`--file` is required for `--source claude-ai`. `--min-confidence` sets the
extractor's floor; `--output json` prints the machine form. The handler is
`_import_from_claude_ai` in `empirica/cli/command_handlers/profile_commands.py`,
which is the code path this page documents.

## memories.json

Claude's memory about the user: a pre-built epistemic profile, returned under
`metadata["memories"]` when the export contains it.

```json
[
  {
    "content": "User is a senior engineer working on...",
    "created_at": "2026-01-10T08:00:00Z"
  }
]
```

The parser passes it through as metadata. Turning those entries into eidetic
facts is not implemented here.
