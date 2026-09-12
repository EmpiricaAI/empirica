---
name: epistemic-editing
description: Use when reviewing a document whose claims must hold up — a paper, a report, a spec, a post, release notes, a proposal. Runs a grounded review pass: sweeps the document for claim classes that fail silently (universal quantifiers, absence claims, numbers that drift between sections, citations that outrun their source, figures that disagree with their data), raises each finding as a flag carrying its severity, how it was grounded, its evidence and a proposed edit, renders the document with the flags spliced in at the paragraphs they concern, and records the reader's accept/reject/defer so the accepted set drives a versioned change. Triggers on "review this document", "check this paper", "fact-check my draft", "does this hold up", "epistemic editing", "grounded review", "before I publish this".
version: 1.0.0
---

<!-- Vendored from `epistemic-editing/` in david/empirica-paper (forgejo, f1b3b06) — edit upstream, not here.
     Ownership ratified 2026-09-12: empirica-paper MAINTAINS the mechanism and the seven targets;
     empirica core HOSTS the shipped skill because the other /epistemic-* skills live here.
     VENDOR ADAPTATIONS (re-apply on every sync — upstream does not carry them):
       1. this notice + `version:` in the frontmatter, to match the plugin's skill convention
       2. `galley.py` invoked via its skill directory, not as a bare relative path: the harness
          runs in the user's project, so a bare `python3 galley.py` resolves against their cwd.
     Diff old-vs-new before overwriting. -->

# Epistemic editing

A document is a pile of claims. Most review looks at the prose. This looks at the claims: what each one rests on, whether anyone checked, and what happens if it is wrong.

The output is not a marked-up draft. It is a **galley**: the document rendered in full with a flag at each paragraph that needs a decision, each flag carrying its evidence and a proposed edit, and a decision recorded per flag. Nothing is rewritten silently — the author decides, and the accepted set drives a versioned change.

## The one rule

**Every flag states how it was grounded, in one of four words, and a flag that was not checked says so on its face.**

| Grounding | Means |
|---|---|
| `ran` | executed a command or query and read the output |
| `read` | opened the source and read the relevant lines |
| `fetched` | retrieved the cited external work and checked it says what the document claims |
| `assumed` | not checked — recorded as unverified, never asserted either way |

`assumed` is not failure. It is the honest label for a flag you could not close, and a reviewer who never writes it is hiding something. What is forbidden is asserting at `read` confidence something you only assumed.

## Run the pass

Work the seven targets. Each exists because this class of defect fails *silently* — the document reads fine and the claim is wrong.

**1. Universal quantifiers.** Grep the document for `all / every / none / never / always / only / each`. For each hit, run the loop over X before the claim stands. This target exists because its author wrote "every practice shows the same sign on every vector" after checking four of thirteen columns. A four-column table does not license a thirteen-column claim.

```
rg -n -i '\b(all|every|none|never|always|only|each|entire)\b' DOC
```

**2. Superlatives and firsts.** `first / novel / unique / no existing / cannot / unprecedented / the only`. Each needs one search that would falsify it, or it softens. Spec-absence is not implementation-absence: "no tool does this" usually means "I did not find one in five minutes."

**3. Numeric consistency.** Inventory every number — counts, percentages, versions, dates, identifiers — with its section and line. Check each against its source *and against every other statement of the same quantity in the document*. Papers contradict themselves across sections far more often than they contradict their data. Watch for one word naming two quantities (a table column and a chart axis both labelled "Evidence", holding different things).

**4. Citation framing.** For every cited work: fetch it. Check three things — that the authors and title are right, that the finding is what the document says it is, and that any quotation is **verbatim**. Paraphrase inside quotation marks is a fabricated quote, and it is the single most damaging defect this pass finds. Check also *what the study measured*: a result on multiple-choice self-evaluation does not support a claim about confidence expressed in words.

**5. Absence and staleness.** Claims of the form "X does not exist", "nothing does this", "currently" and "as of" all rot. Date them or check them. If the document describes a system, check the version it describes against the version that exists.

**6. Figure–data agreement.** Re-derive every plotted value from the underlying data. Figures are where stale numbers survive longest, because reviewers read prose.

**7. Whose error is it.** Type each defect by the question it answers, not by where you found it:

| The defect | Type it as |
|---|---|
| The document states something false about the world | a **finding** — with the true statement |
| The author overclaimed beyond their evidence | a **mistake** — with what would prevent it recurring |
| Nobody can verify it from what is available | an **unknown** — it stays open, it is not resolved by being flagged |
| The author is taking something for granted | an **assumption** — say what would falsify it |
| Register, placement, a judgment call | a **note** — the author's to decide |

## Write the flags

One JSON record per flag:

```json
{"id": "F07",
 "severity": "blocker | should-fix | note",
 "target": "citation framing",
 "anchor": "a verbatim phrase from the paragraph this concerns",
 "grounding": "ran | read | fetched | assumed",
 "title": "one line, the claim not the rationale",
 "issue": "what is wrong, stated so the author can check it",
 "evidence": "the command, the URL, the file:line — what you actually did",
 "edit": "the proposed replacement, or the decision to be made"}
```

**Anchor by phrase, never by line number.** Line numbers written from memory are wrong roughly eight times in ten; the builder resolves the phrase to a line at render time and fails loudly if it cannot find it.

**Severity means consequence, not confidence.** `blocker` = a reader acting on this would be misled (a fabricated quote, a number that is simply wrong). `should-fix` = a real defect with a clear edit. `note` = a judgment call that is the author's to make.

## Render and decide

```bash
python3 <skill-dir>/galley.py --md DRAFT.md --flags flags.json --config config.json --out galley.html
```

`galley.py` ships beside this SKILL.md. The loader prints the skill's base directory — use that
path. A bare `galley.py` resolves against the document's directory and will not be there.

Stdlib Python, no dependencies. It emits a source-line marker before every block, splices each flag after the paragraph its anchor resolves to, and builds the decision layer. Publish `galley.html` as an artifact with `capabilities: {db: {}}` so accept/reject/defer persists and the author's decisions can be read back with `read_db`.

Then: read the decisions, apply the accepted set, name the applied flags in the commit message, leave rejected and deferred flags on record with the author's note. **A published record is never edited in place** — accepted flags produce a versioned update with a changelog entry, because readers have already cited what is published.

## Iterate until it converges

One pass is not the method. Run another: the first pass changes the document, and changed text has new claims. Stop when a pass produces no new flags and the targets are covered — not after a fixed number of passes. Track coverage honestly: what share of claims were verified, quantifiers looped, citations fetched, figures re-derived.

## Two tiers

**Standalone** — everything above. Any Claude Code install, no framework. Measured on this skill's originating pass: 20 of 22 flags, including the one blocker, needed nothing but shell, web fetch, and the document's own data.

**With [Empirica](https://github.com/EmpiricaAI/empirica)** — the same pass, plus: flags persist as typed artifacts in a knowledge graph so a document's review history is retrievable and federates to other practices; the pass runs as a measured transaction (PREFLIGHT → CHECK → POSTFLIGHT) so the *reviewer* is calibrated too; coverage is measured deterministically at close; iteration is gated on confidence rather than a fixed count.

**What the standalone tier does not give you, stated plainly:** the seven targets above are a distillate of defects that a real practice accumulated and recorded. You inherit that distillate frozen. You do not get the loop that *grows new targets from your own mistakes* — that needs a persistent record. The checklist is the compounding; without a graph, it stops compounding.
