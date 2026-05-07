# Empirica Ecosystem Overview

**For end users who want to understand where their data lives**

---

## The Simple Mental Model

When you work with Empirica through Claude (or any AI assistant), there are two kinds of things being stored:

| What you're storing | Where it lives | Example |
|---------------------|----------------|---------|
| **Your actual content** | Where it naturally belongs | Code in GitHub, docs in Google Drive, videos on YouTube |
| **What you know about it** | Empirica databases | "I learned X about client Y" or "This project is 60% complete" |

**Empirica doesn't store your files.** It stores what you've *learned* about them, who they're *for*, and how your *understanding* has changed over time.

---

## Three Layers of Empirica Data

### 1. Global Data (Your Relationships)

**What:** Information about people and organizations you work with
**Where:** `~/.empirica/crm/` on your machine (or cloud if using Empirica Platform)

This is your **CRM** — Client Relationship Memory. It stores:
- **Clients**: Companies, people, organizations
- **Engagements**: Ongoing projects or deals with each client
- **Memories**: What you've learned about each client over time

**Example conversation with Claude:**
> "Remember that Acme Corp prefers email over Slack, and their deadline is Q2"

Claude stores this in your CRM. It's not tied to any specific project — it's about the *relationship*.

### 2. Project Data (Your Work)

**What:** Information about a specific codebase, research project, or body of work
**Where:** Inside each project's `.empirica/` folder

Every project you work on has its own database tracking:
- **Goals**: What you're trying to accomplish
- **Findings**: What you've discovered
- **Unknowns**: What you still need to figure out
- **Sessions**: Your work history and learning progress

**Example conversation with Claude:**
> "I figured out why the API was slow — it's making N+1 queries"

Claude logs this as a *finding* in that project. It stays with the project, travels with the code if you push to GitHub.

### 3. Workspace Data (Your Portfolio)

**What:** Global registry of all your projects with trajectory pointers
**Where:** `~/.empirica/workspace/workspace.db`

If you work on multiple projects, the workspace layer gives you:
- **Project registry**: All projects with paths to their `.empirica/` directories
- **Cross-project patterns**: "I consistently underestimate caching complexity"
- **Anti-patterns**: "Redis approach failed in 3/5 projects — avoid unless X"
- **Knowledge transfer links**: Connect related learnings across codebases
- **Portfolio analytics**: Transaction counts, findings, dormancy detection

**Example conversation with Claude:**
> "What projects have I been neglecting lately?"

Claude checks the workspace database for projects with stale `last_transaction_timestamp`.

> "What have I learned about authentication across all projects?"

Claude searches cross-project patterns and linked findings.

---

## How the Layers Connect

```
┌─────────────────────────────────────────────────────────────┐
│  YOUR ACTUAL CONTENT                                         │
│  (code, docs, videos, images, websites)                     │
│  Lives wherever it naturally lives — NOT in Empirica        │
└─────────────────────────────────────────────────────────────┘
                              │
                    Empirica stores METADATA about it
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  GLOBAL (~/.empirica/)                                       │
├─────────────────────────────────────────────────────────────┤
│  crm/crm.db           │  workspace/workspace.db             │
│  • Clients            │  • Project registry                 │
│  • Engagements        │  • Trajectory pointers              │
│  • Relationship       │  • Cross-project patterns           │
│    memories           │  • Knowledge transfer links         │
└───────────────────────┴─────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  PER-PROJECT (project/.empirica/)                            │
├─────────────────────────────────────────────────────────────┤
│  sessions/sessions.db                                        │
│  • Goals, subtasks                                           │
│  • Findings, unknowns, dead-ends (with transaction_id)      │
│  • Epistemic reflexes (PREFLIGHT/CHECK/POSTFLIGHT)          │
│  • Session history                                           │
│                                                              │
│  Stays with project, travels with code via git notes        │
└─────────────────────────────────────────────────────────────┘
```

---

## Many-to-Many: Clients ↔ Projects

The real power is in the connections:

- **One client** can be connected to **many projects**
  > "Acme Corp hired us for the mobile app AND the backend API"

- **One project** can serve **many clients**
  > "Our open source library is used by Acme, Beta Inc, and Gamma LLC"

When you tell Claude:
> "Log this finding about the API for Acme Corp"

Empirica stores the finding in the **project** AND links it to the **client**. Later you can ask:
> "What have we learned about Acme Corp across all projects?"

---

## Natural Language Examples

### Client/Relationship (→ CRM)

> "Remember that Sarah at Acme prefers video calls"
> "Acme Corp's budget is around $50k"
> "Our main contact at Beta Inc is leaving next month"

### Project/Work (→ Project DB)

> "I discovered the database schema uses UUID everywhere"
> "We still need to figure out the authentication flow"
> "The caching approach didn't work — Redis was too slow"

### Cross-cutting (→ Both)

> "Log this finding about performance for the Acme project"
→ Finding goes to project DB, link created to Acme client in CRM

> "What does Acme care most about?"
→ Queries CRM for Acme's memories + linked findings from all projects

---

## For Users Without Code Projects

**You don't need a git repository to use Empirica.**

If you're using Empirica Platform (web interface), your data lives in the cloud:
- CRM data: Accessible from anywhere
- Project data: Can be a "virtual project" (no code, just knowledge tracking)
- Workspace: Managed through the web UI

The natural language interface stays the same. You talk to Claude, Claude handles the storage.

**Example for a non-technical user:**

> "I'm starting a new client relationship with Acme Corp"
→ Creates client in CRM

> "They're interested in our consulting services for Q2"
→ Creates engagement linked to Acme

> "Had a call today — they're budget-conscious but want premium quality"
→ Logs memory to Acme client

All of this works without touching any code or command line.

---

## Summary Table

| You say... | Empirica stores it in... | Why there? |
|------------|-------------------------|------------|
| "Remember this about [client]" | CRM (global) | Relationship knowledge follows you |
| "I discovered [thing] in this project" | Project DB | Discovery belongs to the codebase |
| "Link this finding to [client]" | Both (with link) | Connects the two layers |
| "What do I know about [client]?" | CRM + linked findings | Queries across layers |
| "Show me my active projects" | Workspace | Portfolio overview |

---

## Sessions vs Transactions

A key distinction that helps understand how Empirica tracks learning:

| Concept | What It Is | Purpose |
|---------|------------|---------|
| **Session** | A context window | Tracks when Claude's memory compacts |
| **Transaction** | PREFLIGHT→work→POSTFLIGHT | Measures actual learning |

**Sessions** are the AI's "context windows" — they end when memory compacts. They're internal bookkeeping.

**Transactions** are the real unit of epistemic measurement:
1. PREFLIGHT: "Here's what I know before starting"
2. Work: Findings, unknowns, dead-ends logged
3. POSTFLIGHT: "Here's what I learned"
4. Post-test: Grounded verification against evidence

All noetic artifacts (findings, unknowns, dead-ends) have a `transaction_id` linking them to this measurement window. This enables:
- "Show me everything learned in transaction X"
- "What was the learning delta for this chunk of work?"
- "How did my confidence change during this investigation?"

---

## The Key Insight

**Empirica separates WHAT you're working on from WHAT you've learned about it.**

Your files, code, documents, and media stay where they are. Empirica tracks the *knowledge* — what you've discovered, what's still unknown, who cares about it, and how your understanding has grown.

This means:
- Your actual work is never locked into Empirica
- Your knowledge persists even if the original content changes
- You can connect insights across different projects and clients
- Claude can help you remember and build on what you've learned

---

## Further Reading

- [Natural Language Guide](EMPIRICA_NATURAL_LANGUAGE_GUIDE.md) — How to talk to Claude about Empirica concepts
- [Taxonomy](../../reference/TAXONOMY.md) — The complete vocabulary (technical reference)
- [Getting Started](FIRST_TIME_SETUP.md) — Initial setup instructions
