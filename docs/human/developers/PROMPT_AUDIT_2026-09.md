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
