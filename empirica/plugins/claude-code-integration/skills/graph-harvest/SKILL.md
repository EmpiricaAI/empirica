---
name: graph-harvest
description: "Use when the user says '/graph-harvest', 'harvest this into the graph', 'back-fill artifacts from these docs', 'what does this conversation know that the graph does not', 'mine our old notes', or wants decisions, assumptions, unknowns and findings that already exist in material (documents, past conversations, code, designs) turned into typed, linked artifacts. The material kinds (documents, code, commits, reviews, mesh threads, conversations, designs, meetings, correspondence, data work, incident records, contracts) and which matter most for each project type come from the harvest map. A reasoning pass, not an extractor: the practitioner reads the material and decides each artifact; tools only list the material and write what was decided. Harvested artifacts are tagged 'harvested' and never counted in calibration."
version: 0.2.0
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
   (`evidence`, `grounded_by`, `resolves`), do not duplicate. **An `invalidates` edge retires a finding it points at**:
   give it `"metadata": {"kind": "stale" | "superseded" | "retracted" | "mistyped", "reason": "..."}` (`stale` when the
   old finding was true when written, `retracted` when it was wrong), or it closes with no kind recorded. Pointed at an
   assumption or a decision, the edge is stored and the target stays live: close those with `resolve-artifacts`. A contradiction you are not
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

## Which material: the harvest map

What a harvest can read, and which material matters most for each kind of practice, is in the map:
`<Install>/empirica/config/harvest_sources.yaml` (`Install` is the line `empirica --version` prints). Do not work from
memory of it; read it.

1. Read the practice's type and domain: `yq '.type, .domain' .empirica/project.yaml`.
2. In the map, the type's `project_types` entry lists the material kinds that hold most, most valuable first; offer
   those first and the other kinds as 'also'. A legacy type (`software`, `product`, ...) is read through
   `legacy_types`; a type or domain the map does not list gets every kind and the default note, but a practice doing
   client work keeps the consulting consent rule (a yes per item, kept local) whatever its domain is called. The `domains` entry says how the domain changes the harvest; the
   `work_types` entry for the work at hand says when a harvest pays.
3. Each kind says what it holds, how to read it, and its consent. The person names the material (`/graph-harvest docs
   docs/architecture/`, `/graph-harvest commits v1.16.0..v1.17.0`); with no argument, propose the first kind for this
   practice and what you would read.

**Consent** is the kind's `consent` value, and it is never yours to grant:

| Value | Meaning |
|---|---|
| `none` | the practice's own material (its docs, code, history, mesh threads) |
| `practice_opt_in` | a setting the subject practice turns on itself; for conversations, `cockpit: read_transcripts: true` in the `.empirica/project.yaml` of the practice whose conversations would be read. Off by default; if it is off, say so and ask the person to enable it there, or to paste the part that matters |
| `per_item_yes` | someone else's material (meetings, correspondence, contracts): read each item only with the person's yes for it, and keep what is harvested local |

Long material: one subagent per document, module or conversation, each returning proposed nodes and edges; a
subagent's list is a draft for you to check, not a decision.

## After the harvest

1. Take the baseline query again and compare: assumptions per 100 findings, decisions, `sourced_from` links, linked
   findings.
2. `empirica blindspot-scan`: new premises can expose gaps.
3. Close the harvest goal with the counts.
4. **Report**: per unit, what was added and linked; the before and after counts; the falsifiers to register; anything
   that contradicted the current graph.
