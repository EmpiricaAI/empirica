---
name: ewm-interview
description: "Use when the user says '/ewm-interview', 'run EWM interview', 'create workflow protocol', 'set up my workflow', 'interview me for EWM', or wants to create a personalized AI collaboration protocol. Guided multi-choice interview that produces workflow-protocol.yaml, renders it as a clickable artifact, and offers to provision the practices it implies."
version: 0.2.0
---

# /ewm-interview — Epistemic Workflow Manager Interview

Produce a **workflow-protocol.yaml** describing how this person wants to work
with AI — then actually set up what it implies.

## The rule that governs the whole interview

**Ask with options, never with a blank.** Every question goes through
`AskUserQuestion` with 2–4 concrete, pickable options. The tool supplies its own
"Other" path, so a free-form answer is always available without you offering one
— do not add an "Other"/"Something else" option yourself, it duplicates the
built-in.

This matters more than it looks. "What are your main constraints?" asked into a
void gets a shrug or a paragraph you cannot map to a field. The same question as
four options gets an answer in one click, and the options themselves TEACH what
the field means. Prose questions are what made v0.1 a form-fill wearing a
conversation's clothes.

Three rules for the options you write:

- **Make every option genuinely pickable.** An option nobody could choose is
  filler; it makes the list look considered while narrowing the real choice.
- **Put a recommendation first** when there is a sane default, and say so:
  `"Collaborative — check in on approach (Recommended)"`.
- **`multiSelect: true`** when answers are not exclusive (domains, tools,
  non-negotiables). Most of this interview is multi-select.

Batch related questions into ONE `AskUserQuestion` call — it takes up to 4. Five
calls of 3–4 questions each covers the whole interview.

---

## Phase 1 — Goals

One call, up to 4 questions:

| Ask | Shape | Options to offer |
|---|---|---|
| What are you mainly trying to accomplish right now? | single | Ship a product / feature · Research & understand a problem space · Grow an organisation or practice · Operate & maintain something live |
| What does "done" look like for that? | single | A shipped, working thing · A decision I can defend · A repeatable process · Learning I can reuse |
| What is your binding constraint? | multi | Time · Money / headcount · Missing knowledge · Regulatory / compliance · Dependence on other people |
| Horizon? | single | This week · This quarter · This year · Open-ended |

Capture into `goals.primary[]` (description, success_criteria, timeline) and
`goals.secondary[]`.

## Phase 2 — Domains & expertise

| Ask | Shape | Options |
|---|---|---|
| Which domains are you expert in? | multi | Domains inferred from the repo and their answers so far, plus generic ones |
| Which are you actively learning? | multi | same list |
| Where do you want AI to carry the most weight? | multi | same list |

**Infer the option list — do not ask people to type their own field.** Read the
repo first: languages present, `docs/` topics, the project's own
`.empirica/project.yaml`. Offer what you found. This is the single biggest
friction reduction in the interview.

Capture `domains.expert[]`, `domains.learning[]`, `domains.novice[]`.

## Phase 3 — Tools & connections

| Ask | Shape | Options |
|---|---|---|
| Which of these do you actually use daily? | multi | Detected MCP servers + GitHub / Forgejo · Slack · Google Drive · Linear / Jira · Notion |
| Where does your code live? | single | GitHub · Forgejo (self-hosted) · GitLab · Local only |
| Is this practice on the Cortex mesh? | single | Yes — registered · Yes — should be, not yet · No, standalone |

**Detect before asking.** Read `~/.claude/mcp.json` (or the harness equivalent)
and offer what is configured rather than a generic menu. Mark detected entries
so the user is confirming, not recalling.

The last two questions are load-bearing — they feed Phase 6.

## Phase 4 — Work preferences

| Ask | Shape | Options |
|---|---|---|
| How should we split the work? | single | Equal partners — check in on approach (Recommended) · You lead, I execute · I run autonomously, you review outcomes |
| When should I act without asking? | multi | Research & investigation · Code implementation once you have said go · Refactors & cleanup · Anything reversible |
| What must ALWAYS wait for you? | multi | Anything sent outside the org · Architecture with business impact · Spending money · Legal / contractual · Deleting things |
| When I think you are wrong? | single | Direct and factual, no hedging (Recommended) · Gentle reframe · Socratic questions |

Capture `work_preferences.*` and the three `task_splitting` lists.

Note the asymmetry: **"act without asking" is a floor, "always wait" is a
ceiling.** If an item appears in both, the ceiling wins — say so rather than
silently resolving it.

## Phase 5 — Trust & non-negotiables

| Ask | Shape | Options |
|---|---|---|
| How should trust start? | single | Start collaborative, widen as it is earned (Recommended) · Start restricted · Start open, pull back if needed |
| What earns more autonomy from you? | multi | Accuracy that holds up · Flagging its own gaps · Catching problems unprompted · Admitting mistakes fast |
| Absolute non-negotiables? | multi | Never act against my interests · Never hide uncertainty behind agreeable language · Never take irreversible actions unasked · Never send anything externally without approval |

Capture `trust_building.*`.

---

## Phase 6 — Provision what the answers imply

**This is the part v0.1 was missing.** An interview that produces a YAML file
and stops has described a setup rather than performed one.

`empirica provision-practice` already does the whole chain, idempotently:
`mkdir` → `project-init` → patch `.empirica/project.yaml` (ai_id / tenant / org
/ substrate) → `project-register` with Cortex → optional Forgejo backup remote.
Safe to re-run; every step no-ops if already done.

So: from Phases 1–3, propose the practices their answers imply — a separate
practice per distinct domain of work, which is the model Empirica is built on
(a practice is an epistemic specialization, not a folder).

**Always dry-run first, and show the output before doing anything:**

```bash
empirica provision-practice <name> --dry-run --output json
```

Then ask — with options, like everything else:

> Provision these? — `empirica-research`, `empirica-outreach`
> · Yes, both · Just the first · Let me adjust the names · No, not now

Only on an affirmative:

```bash
empirica provision-practice <name> \
  --tenant <tenant> --org <org> \
  [--forgejo-owner <owner> --forgejo-host <ssh-url>] \
  [--no-cortex]
```

Where the flags come from Phase 3:

| Phase 3 answer | Flag |
|---|---|
| Code lives on Forgejo | `--forgejo-owner` + `--forgejo-host` (ask for both — they are not guessable) |
| Code lives on GitHub / GitLab / local | omit the Forgejo flags |
| Not on the mesh / standalone | `--no-cortex` |
| On the mesh | omit `--no-cortex`; `--tenant` / `--org` default from the current directory's project.yaml |

**Report per practice what actually happened** — provisioned, already existed,
or failed and why. A rollup "done!" over a partial failure is the shape this
whole system exists to prevent.

---

## Output

### Where to write it — ask, do not assume

The loader searches **project directory first, then `~/.empirica/`**. That
ordering has a consequence worth stating out loud, because it is silent:

> **A project-local protocol SHADOWS the user's global one for that project.**

That is a feature when someone wants different working agreements on a client
repo, and a trap when they meant to update their global profile and quietly
stopped using it everywhere else.

So ask, with the default first:

> Where should this live? · `~/.empirica/workflow-protocol.yaml` — applies
> everywhere (Recommended) · This project only — overrides your global protocol
> here

If they pick project-local and a global one already exists, **say what will be
shadowed** before writing.

### Shape

```yaml
# Epistemic Workflow Protocol
# Generated by EWM Interview v0.2.0 — {date}

user_profile:
  name: "{name}"
  role: "{role}"
  created: "{date}"
  last_updated: "{date}"

goals:
  primary:
    - description: "{goal}"
      success_criteria: ["{criterion}"]
      timeline: "{timeline}"
  secondary: ["{goal}"]

domains:
  expert: ["{domain}"]
  learning: ["{domain}"]
  novice: ["{domain}"]

tools:
  {category}: "{name}"        # Maps to: {mcp_server}

mesh:                          # Phase 3 — omit the block entirely if standalone
  cortex: true
  tenant: "{tenant}"
  org: "{org}"
  forgejo:
    owner: "{owner}"
    host: "{ssh_url}"

practices:                     # Phase 6 — what was actually provisioned
  - name: "{ai_id}"
    provisioned: true
    domain: "{domain}"

work_preferences:
  ai_autonomy_level: "{autonomous|collaborative_with_checkpoints|assistant_mode}"
  uncertainty_surfacing: "{always_explicit|when_material|minimal}"
  pushback_style: "{direct_and_factual|gentle_reframe|socratic}"
  task_splitting:
    ai_autonomous: ["{task}"]
    ai_with_checkpoint: ["{task}"]
    human_only: ["{task}"]

trust_building:
  current_level: "{establishing|building|established|high_trust}"
  autonomy_earned_through: ["{demonstration}"]
  non_negotiables: ["{boundary}"]

modules:
  active: []
  available: []
```

Omit blocks with nothing in them. An empty `mesh:` block is a claim that the
mesh was considered and configured, which would be false.

### Render it as an artifact

Do not paste 80 lines of YAML into the conversation and ask "does this look
right?" — nobody proofreads YAML in a terminal.

Publish it with the **Artifact** tool as a readable page: the working agreement
in prose, grouped by section, with the raw YAML at the end for anyone who wants
it. The user gets a link they can open, keep and re-read later — and a review
they will actually do produces corrections, which is the point.

Then ask for corrections **before** writing the file, with options:

> · Looks right, save it · Change my autonomy settings · Change the
> non-negotiables · Add a goal

---

## After saving

1. **Save** to the location chosen above.
2. **Report** what was provisioned in Phase 6, per practice, honestly.
3. **Log it** — `empirica finding-log` with what the protocol covers (N goals,
   N domains, N practices provisioned).
4. **Say how to change it** — re-run `/ewm-interview`, or edit the YAML
   directly; it is theirs and it is plain text.

---

## Interview discipline

- **5–10 minutes, not 30.** Five `AskUserQuestion` calls. If you are on your
  eighth, you are interviewing rather than helping.
- **Infer before asking.** Repo languages, configured MCP servers, existing
  `project.yaml` — every one of those is a question you do not have to ask.
- **Never ask what you can detect.** Confirming is cheap; recalling is not.
- **Hedging is a signal, not an answer.** "It's complicated", "kind of", "I
  guess" is CONTEXTUAL pushback — ask a narrower question with narrower options
  rather than accepting the vagueness into a field. Do not mirror hedged
  language back.
- **Hold structure against emotional pushback; update on genuine new context.**
  Full framing in `/epistemic-persistence-protocol`.
- Once Phase 4 captures `pushback_style`, **use it for the rest of the
  interview.** The protocol starts applying the moment it is known.

## Design principles

1. **Options over blanks** — the option list teaches the field.
2. **Detect over ask** — inference is the friction reduction that matters.
3. **Provision, don't describe** — Phase 6 is why this exists.
4. **Transparent** — they see it, own it, and can edit it as plain text.
5. **Evolvable** — re-runnable, and `provision-practice` is idempotent so
   re-running is safe.
