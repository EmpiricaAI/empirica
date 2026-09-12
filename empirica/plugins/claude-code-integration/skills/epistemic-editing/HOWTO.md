# Epistemic editing — how to run it

A walkthrough for a real document, end to end. Fifteen minutes for a short piece, a few hours for a paper with citations and data.

---

## What you need

- **Claude Code**, and a document in markdown (LaTeX and HTML work too — they convert first).
- **`galley.py`**, in this directory. Stdlib Python 3, no install.
- **Somewhere to publish the galley** so decisions persist — a claude.ai artifact with the `db` capability. Without it the galley still renders and decisions fall back to browser storage, which is fine for reviewing alone and useless for reviewing with someone else.
- **Optional: [Empirica](https://github.com/EmpiricaAI/empirica)**, if you want the pass measured and the flags kept.

---

## The five steps

### 1. Say what the document must not get wrong

Before the sweep, decide the consequence model, because it changes what counts as a blocker:

- **A published record** (a paper with a DOI, a shipped spec, a press release): accepted flags become a *versioned update with a changelog*. You never edit what readers have cited. A false claim here is a blocker.
- **A draft** (a chapter, a proposal, a post before publishing): accepted flags are applied in the next commit and named in its message. A blocker is anything that would embarrass you after publication.

State it at the top of the galley. Every decision the reader makes inherits it.

### 2. Run the seven targets

Ask Claude to run an epistemic-editing pass, or work the targets yourself. In practice the sweep is mechanical and the judgment is not:

```bash
# quantifiers and superlatives — the mechanical part
rg -n -i '\b(all|every|none|never|always|only|each|entire)\b' DRAFT.md
rg -n -i '\b(first|novel|unique|no existing|cannot|unprecedented)\b' DRAFT.md

# every number, with its line, to check against sources and against each other
rg -n -o '[0-9][0-9,.]*%?' DRAFT.md
```

Then the part that needs a mind: for each hit, is the claim true, and how would I know? Fetch every citation. Re-derive every figure. Check each number against the others.

**The discipline that makes this worth doing:** when you cannot check something, write the flag anyway and label it `assumed`. A review that only reports what it could verify is a review that hides its own gaps.

### 3. Write the flags

A JSON array; see the schema in `SKILL.md`. Two rules that save rework:

- **Anchor by phrase**, not line number. `"anchor": "the calibration record is the measured divergence"`. The builder resolves it at render time.
- **Put the proposed edit in the flag.** A flag that only objects makes the author do the work twice. A flag that proposes the replacement can be accepted with one click.

### 4. Render and publish

```bash
python3 <skill-dir>/galley.py --md DRAFT.md --flags flags.json --config config.json --out galley.html
```

It prints `unresolved anchors: none` when every flag found its paragraph. **If it reports an unresolved anchor, fix the anchor — do not ship the build.** Chain it so a failed build cannot reach your commit:

```bash
python3 <skill-dir>/galley.py ... && git add galley.html && git commit -m "..."
```

`config.json` sets the title, the subtitle, the consequence-model banner, and a "checked and consistent" list — say what you verified and found *clean*, not only what you flagged. A reviewer needs to know the numbers were checked, not just which one was wrong.

Publish `galley.html` as an artifact with `capabilities: {db: {}}`. Send the link.

### 5. Read the decisions back and apply them

The reader clicks accept / reject / defer on each flag, optionally with a note. Those land in the artifact's store. Read them with `read_db` on the `decisions` collection, apply the accepted set, and name each applied flag in the commit message. Rejected and deferred flags stay on record with the note — the record of what was *considered and declined* is worth as much as the record of what changed.

Then run step 2 again on the changed text. Stop when a pass yields no new flags.

---

## What it finds, from a real pass

The first document this was run on was a paper of the practice's own — its PDF publicly downloadable under a DOI since March 2026, though not peer-reviewed and not on a preprint server:

| | |
|---|---|
| **1 blocker** | A sentence in quotation marks, attributed to a cited paper, that **does not appear in the version of that paper we could read**. Found by fetching the preprint and searching its full text; the journal version is paywalled, and the flag says so rather than claiming more than it checked. It had survived drafting, twenty-nine revision commits, and publication. |
| **2 misattributions** | A citation crediting the wrong author entirely, and another crediting a paper with a result it does not contain. |
| **1 unreproducible headline number** | A figure in the abstract that cannot be derived from the dataset the paper ships. |
| **3 internal contradictions** | The same quantity stated two ways in different sections — five months versus six, two ungrounded items versus four, one rate copied into a second row. |
| **1 stale architectural claim** | True of the version described, false of the version running. |

Every one of these reads fine in prose. That is the point: this pass targets the defects that *do not look like defects*.

---

## Honest limits

**It inherits a checklist; it does not grow one.** The seven targets are a distillate of defects a working practice accumulated and wrote down. You get them frozen. Growing new targets from your own mistakes needs a persistent record of those mistakes — which is what the Empirica tier adds and the standalone tier does not.

**It cannot check what it cannot reach.** A claim about private data, an unlinked internal system, or a paywalled source gets `assumed`, and `assumed` flags are the ones a human must close.

**The reviewer is not exempt.** This skill's own originating pass produced a wrong flag (a line-number set written from memory, eight of ten wrong) and a wrong finding (a counter described as "dead" that was merely narrow). Both were caught by someone re-deriving the claim from outside. Run the pass on the pass.

**A review is not an endorsement.** Clean output means the checked claims held. It does not mean the argument is sound, the method is right, or the conclusion follows.

---

## Where this came from

The mechanism emerged in `empirica-paper` on 2026-09-06, reviewing its own practice's first paper — publicly released under a DOI, not peer-reviewed. Full spec, including the graph-integrated design: `paper2/tools/GROUNDED-REVIEW.md`. It is the noetic→praxic loop — investigate, gate, act, adjudicate — pointed at a document instead of a codebase. A paper turns out to be gardenable.
