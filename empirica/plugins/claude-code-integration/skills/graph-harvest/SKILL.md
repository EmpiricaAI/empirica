---
name: graph-harvest
description: "Use when the user says '/graph-harvest', 'harvest this into the graph', 'back-fill artifacts from these docs', 'what does this conversation know that the graph does not', 'mine our old notes', or wants decisions, assumptions, unknowns and findings that already exist in material (documents, past conversations, code, designs) turned into typed, linked artifacts. Options: docs <paths> | conversations [practice] | code <path> | visual <file or page>. A reasoning pass, not an extractor: the practitioner reads the material and decides each artifact; tools only list the material and write what was decided. Harvested artifacts are tagged 'harvested' and never counted in calibration."
version: 0.1.0
---

# /graph-harvest — put what existing material knows into the graph

Documents, past conversations, code and designs hold decisions, assumptions and open questions that the graph never
saw. Harvesting reads that material and records it as typed, linked artifacts, so forecasting, blindspot scans and
retrieval can use it.

**This is reasoning, not extraction.** No regex, no keyword pass, no new verb: you read, you decide each artifact, and
`log-artifacts` writes what you decided. A routed skill's linters are evidence you read, never artifact generators.

## Before you start

1. **Load `/artifact-graph`.** Its rules are the harvest's rules: type by the question each artifact answers, split
   what the material observed from what it supplied, connect to PRIOR artifacts, give anything external a source.
2. **Name the harvest a goal**: `goals-create --objective "Harvest: <material>" --description "<what, why, which
   option>"`. Harvested artifacts attach to it.
3. **Take the baseline**, so the run can show what it changed:

```bash
DB=.empirica/sessions/sessions.db
sqlite3 "file:$DB?mode=ro" "select
  (select count(*) from project_findings where coalesce(is_resolved,0)=0) as findings,
  (select count(*) from assumptions) as assumptions,
  (select count(*) from decisions) as decisions,
  (select count(*) from artifact_edges where relation='sourced_from') as sourced_from,
  (select count(*) from project_findings f where exists (select 1 from artifact_edges e
     where e.from_id=f.id or e.to_id=f.id)) as linked_findings"
```

## What gets the `harvested` tag

`harvested` means **the material holds it and you did not observe it now**: a decision a design doc records, an
assumption a plan rests on, a result a report states. Every node of a harvest carries `"epistemic_source":
"harvested"`, and Empirica stores it outside every transaction, so calibration never counts it.

What you **verify now** (you ran the command, read the code at HEAD) is a live observation: tag it `search` and log it
as ordinary work, not as part of the harvest. A claim the material makes that you did not check is not a finding you
made: it is the material's finding (say whose, via its source), or, when the work leans on it, an **assumption**.

## The pass, per unit (a document section, a conversation, a module, a diagram)

1. **Read it whole.** List what it claims, chose, took for granted, and left open.
2. **Look for each in the graph first** (`empirica project-search --task "<claim>"`). Already there: link to it
   (`evidence`, `grounded_by`, `resolves`), do not duplicate. **An `invalidates` edge retires its target**: give it
   `"metadata": {"kind": "stale" | "superseded" | "retracted", "reason": "..."}` (`stale` when the old artifact was
   true when written, `retracted` when it was wrong), or it closes with no kind recorded. A contradiction you are not
   sure of is an `evidence` edge plus an unknown, not an `invalidates`.
3. **Type each new one by its question** (`/artifact-graph`). What material holds most and graphs miss most:
   - **decisions** with the alternatives and what would reverse them; when the material gives no reversal condition,
     log the decision and an **unknown** asking for it;
   - **assumptions**: what the material takes for granted; give a confidence that matches the material, not yours;
   - **unknowns**: questions raised and never answered;
   - **dead ends**: approaches the material tried and dropped, with why.
4. **Register the material once**: `empirica source-add --title "<doc or conversation>" --path <file> --noetic`
   (`--url <link>` for a page), and give every node a `sourced_from` edge to it.
5. **Write the unit in one call**: `empirica log-artifacts -` with its nodes and edges, every node `harvested`, and its
   `visibility` left local. Put the harvest goal's id in the payload (`"goal_id"`) so every node attaches to it.
6. **Falsifiers wait.** An assumption worth testing gets a falsifier at your next PREFLIGHT or CHECK (`falsifiers`,
   with a `query`); list them in the report so they are not lost.

Quality over count: one decision with its reversal condition beats ten findings that restate the document.

## Options

| Option | Material | Notes |
|---|---|---|
| `docs <paths>` | specs, design notes, READMEs, reports, meeting notes | Section by section. Long documents: one subagent per document, each returning its proposed nodes and edges for you to check and write (a subagent's list is a draft, not a decision) |
| `conversations [practice]` | past Claude Code conversations of a practice | **Only if that practice allows it**: `cockpit: read_transcripts: true` in its `.empirica/project.yaml`. Off by default, and set by the practice whose conversation would be read, never on its behalf: if it is off, say so and ask the person to enable it or to paste the part that matters. Transcripts are `~/.claude/projects/<the practice path with / as ->/*.jsonl`; read the human's and the assistant's text, skip tool output. Harvest the human's rulings (decisions, with their reason), corrections (the practitioner's **mistakes**, with a prevention), and questions left unanswered (unknowns). Skip what that session already logged (compare with its artifacts). A preference about how to work is memory, not an artifact |
| `code <path>` | a module or repository | Run `/code-audit` or `/architecture-review`; their output is a live observation (`search`), not a harvest. Harvest only what the code *records*: decisions and assumptions written in comments, docstrings and ADRs |
| `visual <file or page>` | diagrams, designs, published pages | Read the image (`Read`) or the page (the Artifact tool's read). Harvest the decisions and assumptions it depicts; a diagram that disagrees with the code is a finding to verify, not a harvest |

Material from outside the practice (a client's documents, someone else's conversation) is harvested only with the
person's yes for that material. Harvested artifacts stay `local`; sharing one is a separate choice.

## After the harvest

1. Take the baseline query again and compare: assumptions per 100 findings, decisions, `sourced_from` links, linked
   findings.
2. `empirica blindspot-scan`: new premises can expose gaps.
3. Close the harvest goal with the counts.
4. **Report**: per unit, what was added and linked; the before and after counts; the falsifiers to register; anything
   that contradicted the current graph.
