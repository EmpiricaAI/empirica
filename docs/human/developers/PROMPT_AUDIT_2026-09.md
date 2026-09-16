# Prompt audit — `empirica-system-prompt-lean.md`

**Target:** `empirica/plugins/claude-code-integration/templates/empirica-system-prompt-lean.md`
— the artifact core owns and ships to every seat. 7,199 words / 746 lines.

**Procedure:** `anthropics/skills → skills/claude-api/shared/prompt-audit.md`, fetched
this session. The skill is absent from both local bundles (2.1.220, 2.1.263).

**Scope note.** `~/.claude/empirica-org-prompt.md`, `-cortex-prompt.md` and
`-crm-prompt.md` are distributed by cortex / mesh-support, not core. Audited only
what core can change.

---

## Headline: this prompt is already largely clean

The procedure's primary target is pressure language, and there is almost none:

| Pattern | Count | per 100w |
|---|---|---|
| `MUST` | 1 | 0.01 |
| `DO NOT` / `Do NOT` | 3 | 0.04 |
| `ALWAYS` | 1 | 0.01 |

209 all-caps runs sounds alarming and decomposes to **93 protocol nouns**
(`PREFLIGHT`, `CHECK`, `POSTFLIGHT`, `NOETIC`, `PRAXIC`, `SER`, `ECO`) and 116
emphasis runs, the top being `NOT` ×7 and `BEFORE` ×5 — contrastive emphasis on
genuine distinctions, not volume.

**Most of the bulk is Keep by the procedure's own rules.** The vector definitions,
artifact-type vocabulary and transaction discipline are *environment and product
facts the model cannot access elsewhere* — rule 1, "context is never cruft", and
rule 2, "cruft ≠ length". I am not proposing to shorten this prompt for its own sake.

---

## Findings

### F1 — Maintainer-facing comment shipped into the runtime prompt · **HIGH** · remove

`templates/empirica-system-prompt-lean.md:451`

> `<!-- DO NOT CUT THE MESH BULLET ON SINGLE-HOME GROUNDS. A trim table may mark it a duplicate of constitution §V…`

A 9-line note addressed to *whoever next edits the file*, explaining why a trim
would be wrong. **It survives rendering** — verified present in the installed
`~/.claude/empirica-system-prompt.md`. So every seat, every session, carries ~90
tokens of instruction to an audience that is never in the room.

Pattern: **fossil / patch accretion** (1d) — content added to defend against a
past edit rather than to steer the current one.

The reasoning is correct and worth keeping. But **the enforcement is already a
test** — `tests/test_always_loaded_mesh_steers.py`, whose docstring makes the same
argument at more length. The comment is a duplicate of a guard that actually
fires. An editor who cuts the bullet gets a red test; they do not need a second
copy shipped to users.

**Action:** delete from the template; the test docstring is the home. Add the
file:line pointer to the test so the two stay connected.

### F2 — Output choreography in §REPORTING · **LOW** · keep, flagged for the record

`…:659,670` — "done + next, as bullets keyed to goals/tasks", "Close a transaction
or a plan with bullets, not prose. Two blocks…"

This is a fixed output cadence, which 1f names as a removal candidate. I am **not**
proposing to remove it:

- It encodes a **stated user requirement** — David, 2026-09-12: *"the verbosity
  itself is hindering flow and methodical actions"*. The procedure's keep-list
  covers this: constraints that encode real requirements stay.
- The keep-list also protects *"format-pinning examples on genuinely
  format-sensitive outputs"*, and a multi-practice operator reading many reports
  is a format-sensitive audience.

Recorded because a future audit will flag it again from the pattern alone, and
should find this note rather than re-litigate it.

### F3 — Dated measurements will age into fossils · **MEDIUM** · flag, no edit now

`…:617` (*"measured 2026-07-30 … 1267 meant stale and 1 meant wrong"*), and
similar counts elsewhere (`47% of 728 CHECKs`, `79% of goals`).

These are currently **Keep** — rule 5, "current, demonstrated failures stay", and
they are the *reason* behind a constraint, which is explicitly keep-listed. A bare
instruction without them is weaker.

The risk is temporal, not structural: each is a snapshot whose instruction
outlives its evidence, and nothing re-checks them. The procedure's own step 7
says *"re-audit at each model release"* — these want a re-audit at each **practice**
release too. Not editing them today; naming them as the thing to re-measure.

### F4 — Not found: scaffolds, prefills, word caps · **n/a**

No `think step by step`, no `<scratchpad>` tags, no assistant prefills, no numeric
word ceilings on output. The three `scratchpad` hits are the CLI feature name
(`empirica note` is a scratchpad), not a prompting scaffold — a false positive the
pattern list would otherwise produce.

---

## Proposed diff

Only F1 meets the High/Medium bar for a concrete edit.

- **Remove** the 9-line HTML comment at `:451`.
- **Add** to `tests/test_always_loaded_mesh_steers.py` a pointer naming the
  template line it guards, so the argument and its enforcement stay one unit.

Everything else: keep, with F2 and F3 recorded so the next audit starts from
here rather than from the pattern list.

---

# Second pass — the instructiveness audit

The first pass asked the cruft question (what can be cut). David's redirect asked
the one that matters: **does this guidance instruct a Claude arriving fresh, and
does it carry temporal or historical content meaningless without provenance the
prompt cannot supply?**

The criterion behind it is a design principle worth stating plainly:

> **Empirica is the mechanism for learning, experience and provenance. The prompt
> teaches the behaviour; the graph carries the evidence.**

A measurement frozen in the prompt is one that can never be updated, contradicted
or retracted — which is precisely what the artifact types exist to do. To a fresh
Claude it is also unverifiable: a number it did not produce, cannot check, and
cannot situate.

**The test per item:** does the behaviour survive without the datum? If the
instruction still teaches, the datum belongs in the graph.

## Changed

### I1 — Two frozen measurements, both already in the graph

`:181` carried *"47% of 728 CHECKs arrived within 30 seconds of their PREFLIGHT"*.
`:608` carried *"measured 2026-07-30 … 1267 meant stale and 1 meant wrong"*.

Both already exist as findings — `8bb50db9` and `e4bb8b47` — where they are dated,
retrievable and supersedable. The prompt held **frozen duplicates of artifacts the
practice already carries**, doing the graph's job in the one place it cannot be
re-measured.

Rewritten as self-applicable tests rather than statistics:

- *"The tell is the clock: a CHECK submitted moments after its PREFLIGHT had
  nothing between them to certify. If you cannot name what you learned in that
  gap, you are signing a blank certificate."*
- *"A practice whose resolutions are almost all `stale` was not rarely wrong — it
  had no way to say so. Check your own ratio; near-zero is a reporting artifact,
  not a track record."*

Same force, and now a fresh Claude can run both on itself in the moment.

### I2 — A trigger list followed by a correction to itself

§OPERATIONAL GOVERNANCE gave a broad trigger list, then a table narrating that
*"six of these routed wrong"*, then commentary on why that had been a mistake. A
fresh Claude does not need to know the list was once wrong. It needs the routing.

Collapsed into one table of *when you need → go to*, with the in-file sections
marked as already in context. Every routing fact preserved; the narration of the
editing history removed. Net −80 words, but the point is the shape: the reader no
longer holds a list and a correction over it simultaneously.

## Deliberately kept

Domain vocabulary, the 13 vectors, the artifact-type table, transaction
discipline, the noetic/praxic boundary. None of this is history — it is the
mechanism itself, and a fresh Claude cannot act without it. Rule 1 of the audit
procedure and the redirect agree here: **context is never cruft.**

## What to re-check next time

Whether any *new* measurement has been pasted into the prompt. The pattern
recurs because a fresh number feels like evidence at the moment you write it. The
question to ask is where it will be re-measured — if the answer is nowhere, it
belongs in an artifact and the prompt gets the behaviour it implies.
