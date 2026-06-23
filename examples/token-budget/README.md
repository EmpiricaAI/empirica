# Token Budget Agent

An epistemic agent that analyzes your Claude Code session transcripts to find where tokens are being wasted and recommends specific optimizations.

## The Problem

Most Claude Code users don't know why they hit rate limits. The answer is usually context waste — reading files you've already read, bloated system prompts, running exploration on the expensive model, conversations that run too long before compaction.

This agent analyzes your actual session transcripts and tells you exactly what's happening.

## Usage

```bash
# Copy agent to your Claude Code plugins
cp agent.md ~/.claude/plugins/local/empirica/agents/token-budget.md

# Then in conversation:
# "Analyze my token usage for the last week"
# "Why am I hitting rate limits?"
```

## What It Checks

| Pattern | Impact | How It Helps |
|---------|--------|-------------|
| Large file reads | High | Identifies files read in full when only a section was needed |
| Re-reads | High | Same file read multiple times in one conversation |
| System prompt size | High | Every token in your system prompt multiplies across every message |
| Failed tool calls | Medium | Failed + retry = double the context cost |
| Conversation length | Medium | Longer conversations = more expensive per message |
| Subagent routing | High | Exploration on Opus vs Haiku = 60x cost difference |

## Example Output

```
# Token Usage Analysis

## Summary
- Sessions analyzed: 12
- Estimated waste: 35% of total tokens
- Top optimization: Route exploration to subagents (saves ~40% alone)

## Waste Patterns Found
1. Re-reading collector.py 6 times in one session — ~150K wasted tokens
2. System prompt at 6.3K tokens (lean alternative: 1.2K) — ~5K extra per message
3. 14 failed Bash commands retried — ~28K wasted tokens

## Recommendations
1. Use Explore subagents for file search (Haiku, 60x cheaper) — saves ~200K/week
2. Switch to lean system prompt — saves ~100K/week
3. Use targeted Read with offset/limit instead of full file reads — saves ~80K/week
```

## Requires

- [Empirica](https://github.com/EmpiricaAI/empirica) installed (`pip install empirica`)
- Claude Code session transcripts (`.claude/projects/*/*.jsonl`)
