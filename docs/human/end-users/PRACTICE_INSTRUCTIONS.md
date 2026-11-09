# A practice's own instructions

Every Empirica project is a practice: an outreach practice, a research practice, a codebase. Each needs instructions of its
own, because what good work looks like differs between them. Empirica gives each practice two kinds:

| | Generated facts | The practice's `CLAUDE.md` |
|---|---|---|
| File | `.empirica/practice-facts.md` | `CLAUDE.md` at the repository root (or `.claude/CLAUDE.md`) |
| Written by | Empirica, from `.empirica/project.yaml` and the domain file | The people who work in the practice, with the AI proposing |
| Changes | Only when `project.yaml` or the domain file changes | When the person approves a proposed line |
| Holds | Who the practice is, what kind it is, how its work is judged | Rules of this practice that only its people know |

What changes every session (calibration, items that are due, mail from other practices) is neither of these. It arrives at
session start, after them.

## The generated facts

`empirica project-init` and `empirica project-update` write `.empirica/practice-facts.md`, and session start refreshes it.
It says what the practice's own data already says:

- its `ai_id`, and its address on the mesh when it has one;
- what it is for (the project's description);
- its kind (`--type`) and what that kind means;
- its domain (`--domain`): the domain's description, its advice for working in it, and the checks a transaction runs at each
  criticality.

A practice with no type or domain set says so, with the command that sets it. Do not edit the file: it is rewritten when its
inputs change. To change what it says, change the inputs:

```bash
empirica project-update --type cli --domain research
empirica project-update --add-tag "ai-infrastructure"   # descriptive text goes in a tag, not in --domain
```

The file is written only when its text actually changes, and it holds no timestamps or counts. That is deliberate: see
[Why the static part comes first](#why-the-static-part-comes-first).

## The practice's `CLAUDE.md`

When `project-init` creates a practice that has no `CLAUDE.md` (in either place Claude Code reads one), it writes a short
one:

- a heading with the practice's name;
- `@.empirica/practice-facts.md`, so the facts load with it;
- `@AGENTS.md` when the repository has an `AGENTS.md`, so that file stays loaded too;
- a few sections chosen by the practice's kind, each reading "Not written yet: ask the person, then propose the text."

| Kind | Sections |
|---|---|
| Software (`cli`, `library`, `service`, `web-app`, `desktop-app`, `mobile-app`, `browser-extension`, `infrastructure`) | How it is built and tested · What must not break · How changes are reviewed and released |
| `content` | Voice and audience · Where it is published, and who approves · Sources and claims |
| `research` | Questions it answers · Sources and how claims are cited · What counts as done |
| `operations` | Machines and services it touches · What needs a person before it changes · How a change is undone |
| Anything else | What good work looks like here · What needs a person's approval · Where its work goes |

"What this practice is for" is filled from the project's description when one was written (a description ending in " project", like the generated `<name> project`, is treated as not written).

An existing `CLAUDE.md` is never touched. The file grows by approved changes: when the AI learns something durable about
how this practice works, it proposes the line as a diff and writes it only once the person approves. It holds rules, not
history: what holds now, not how it came to be.

Nothing personal is copied into it. Your own working preferences (the workflow protocol) load at session start from your
home directory; the practice's `CLAUDE.md` is committed and shared with whoever works in it.

### A practice that already has a `CLAUDE.md`

Until its `CLAUDE.md` imports the facts, session start includes them after the cached part of the context and offers the
person a one-line change: add `@.empirica/practice-facts.md` near the top. It is applied only once the person approves.
A practice with no `CLAUDE.md` at all is offered the seed above the same way. Once the import is there, session start adds
nothing for it.

## Why the static part comes first

Claude Code caches the start of the context and reuses it while the start is byte-for-byte the same. `CLAUDE.md` and the
files it imports load at the start, so they are part of that cached prefix; anything that changes between sessions must
come after it, or it invalidates the cache for everything behind it. That is why the facts file carries no timestamps or
counts and is rewritten only when its text changes, and why calibration, due items and mail arrive later, as session-start
context.

## Why not `CLAUDE.local.md`

`CLAUDE.local.md` is a person's own uncommitted file. A practice's instructions belong to the practice and are shared with
everyone who works in it, so they go in its committed `CLAUDE.md`. The [project-scoped install](02_INSTALLATION.md) keeps
Empirica's own per-user parts out of the commit in a different way: its hooks go in `.claude/settings.local.json` and its
prompt in `.empirica/`, both ignored by git.
