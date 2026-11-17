---
name: ewm-interview
description: "Use when the user says '/ewm-interview', 'run EWM interview', 'create workflow protocol', 'set up my workflow', 'interview me for EWM', or wants to create a personalized AI collaboration protocol. A short multi-choice interview, in a depth the person picks, that finds out what work they do, proposes the practices that work needs (drafted from templates and routed like forecast predictions), captures how they want to work, writes workflow-protocol.yaml, and provisions the practices."
version: 0.4.0
---

# /ewm-interview — Epistemic Workflow Manager Interview

Find out **what work this person does**, propose and set up **the practices that work needs**, and capture **how they
want to work with AI**, in that order of importance. Write it to `workflow-protocol.yaml` and provision it.

## Rules for the whole interview

- **Ask with options, never with a blank.** Every question goes through `AskUserQuestion` with 2–4 pickable options; the
  tool adds its own "Other". The options teach what the field means.
- **Recommendation first**, marked `(Recommended)`, when there is a sane default.
- **Batch**: one `AskUserQuestion` call takes up to 4 questions.
- **Detect before asking**: repos, configured MCP servers, an existing `.empirica/project.yaml`. Confirming is cheap,
  recalling is not.
- **Hedging is a signal**: "kind of", "it depends" gets a narrower question, not a vague field.
- **Keep it short.** The person chose a depth; stay inside it.

## Step 0 — Pick the depth

One question, first:

> How much time for setup? · **Quick, about 3 minutes (Recommended)**: your kinds of work, a ready setup, how much the AI
> does on its own · **Standard, about 7 minutes**: adds how you like to work and checks the practices I'm unsure about ·
> **Deep, about 12 minutes**: adds your goals, trust and a full pass over which practices to start with

| Tier | Calls | Phases |
|---|---|---|
| Quick | 2–3 | 1 (work), 2 (practices: confirm the preset), the autonomy question from 3 |
| Standard | 4 | 1, 2 (with the 'ask' questions), 3 |
| Deep | 5–6 | 1, 2, 3, 4 (goals and trust) |

Every tier ends with Phase 5: provisioning. Quick still sets up working practices.

## Phase 1 — The work (every tier)

| Ask | Shape | Options |
|---|---|---|
| Which kinds of work do you do? | multi | the work kinds in `empirica/config/practice_templates.yaml` (`workloads`): building or maintaining software, research and synthesis, writing and content, outreach and sales, client or consulting engagements, operations and infrastructure, data and analysis |
| For each one picked: how often? | single per kind (batch up to 4) | Often (weekly or more) · Sometimes · Rarely |

If they build software, ask which codebases (detect repos first; offer what you found). Each becomes its own builder
practice, typed by what it is (`web-app`, `cli`, `library`, `service`, …).

## Phase 2 — Which practices (every tier): the forecast pattern

Draft the practices as **predictions**, the way `/forecast` drafts directions, from the templates in
`empirica/config/practice_templates.yaml` (`empirica.core.practice_templates.suggest()` does exactly this):

- each work kind suggests its templates (`suggested_by`); **often** grounds a practice strongly (0.85), **sometimes**
  moderately (0.6), **rarely** not enough (0.3);
- **support** is proposed for everyone; a **coordinator** once there are two or more other practices;
- every candidate carries its **reason**: the answer it rests on ("you said research and synthesis: often").

Route each candidate (`route()`), and spend questions only where they change the setup:

| Confidence | What happens |
|---|---|
| ≥ 0.7 | **confirm**: listed together, one tap accepts them all |
| 0.5–0.7 | **ask**: one question each, at most 2–3 (Standard and Deep; Quick drops them to 'add later') |
| < 0.5 | **park**: shown as "you can add these later", not provisioned |

**Presets.** When the confirmed set is close to a preset (`presets`: solo builder, researcher, consultant, small team),
offer it as the first option: "Start with the *consultant* setup: client engagement, research, writing, coordinator,
support (Recommended)". Quick offers the closest preset plus "Adjust".

Name each practice in the person's words: `<them>-research`, `<client>-engagement`, `<repo>`.

## Phase 3 — How you work (Standard and Deep; Quick asks only the first question)

| Ask | Shape | Options | Field |
|---|---|---|---|
| How should we split the work? | single | Equal partners, check in on approach (Recommended) · You lead, I execute · I run autonomously, you review outcomes | `ai_autonomy_level`: `collaborative_equal_partner` · `assistant_mode` · `full_autonomy` |
| When I think you are wrong? | single | Direct and factual (Recommended) · Gentle reframe · Socratic questions | `pushback_style`: `direct_and_factual` · `gentle_reframe` · `socratic` |

Defaults when not asked: `uncertainty_surfacing: always_explicit`, `calibration_mode: single_pass`.

## Phase 4 — Goals and trust (Deep only)

| Ask | Shape | Options | Field |
|---|---|---|---|
| What are you mainly trying to accomplish? | single | Ship a product · Understand a problem space · Grow an organisation · Run something live | `goals.primary[0]` |
| What must always wait for you? | multi | Anything sent outside the org · Spending money · Legal or contractual · Deleting things | `task_splitting.human_only`, `trust_building.non_negotiables` |

A first protocol's `trust_building.current_level` is `establishing`; change it only on a later re-run.

## Phase 5 — Provision what the answers imply (every tier)

`empirica provision-practice` does the whole chain idempotently: directory, `project-init`, identity, registration
(Cortex or the local server), an optional Forgejo backup. Always dry-run first and show the result:

```bash
empirica provision-practice <name> --dry-run --output json
```

Or, once the protocol is written, all of them at once: `empirica provision-practice --from-protocol <file> --dry-run`.
Then one question: provision these? · Yes, all · Let me adjust · Not now.

Per practice, from its template and the answers:

| From | Flag |
|---|---|
| the template's `type` (a builder: the codebase's own type) | `--type <type>` |
| the template's `for`, in the person's words | `--description "<sentence>"` |
| the template's `domain` | `--domain <name>` |
| terminal: Claude Code / ecodex / both | `--cockpit-profile <name>`, plus `--launch ecodex` or `--second-pane ecodex` |
| desktop or web app only | no cockpit flags |
| a model other than the harness default | `--launch "claude --model <model>"` |

Tenant, org and Forgejo come from the person's sign-in and these flags, never from a protocol file. **Suggest** the
template's `skills`; install nothing unread, and only with the person's yes for that download.

Report per practice what happened: provisioned, already existed, or failed and why. Then, when a cockpit profile was
written, hand them the command from the last call (`empirica cockpit launch --profile <name>`), with two things for day
one: practices are the tabs at the top, and clicking the line under any pane opens the cockpit's commands (`?` for the
guide). Do not launch it for them.

## Output

**Where:** ask, default first: `~/.empirica/workflow-protocol.yaml` (everywhere, Recommended) · this project only (it
shadows the global one here; say so if one exists).

**Shape:** the contract is `empirica/schemas/workflow_protocol.schema.json` (0.4.0); a field it does not define is
refused. Omit blocks with nothing in them.

```yaml
schema_version: "0.4.0"

user_profile:
  name: "{name}"
  role: "{role}"
  created: "{date}"

practices:
  - name: "{ai_id}"
    template: "{template}"            # from practice_templates.yaml
    reason: "{the answer it rests on}"
    type: "{project type}"
    description: "{one sentence}"
    domain: "{domain}"
    harness: "{claude-code|ecodex|both|none}"
    model: "{model}"                  # omit for the harness default
    skills: ["{skill}"]               # suggestions only

work_preferences:
  ai_autonomy_level: "{collaborative_equal_partner|collaborative_with_checkpoints|ai_first_efficiency_maximizing|full_autonomy|assistant_mode}"
  uncertainty_surfacing: "{empirica_managed|always_explicit|when_material|minimal}"
  pushback_style: "{direct_and_factual|gentle_reframe|socratic}"
  calibration_mode: "{single_pass|multi_round}"
  task_splitting:
    human_only: ["{task}"]

goals:                                 # Deep only
  primary:
    - description: "{goal}"

trust_building:                        # Deep only
  current_level: establishing
  non_negotiables: ["{boundary}"]
```

**Review before saving.** Publish it as a readable page with the Artifact tool (the practices first, each with its
reason; then how they work; the YAML at the end), and ask: Looks right, save it · Change the practices · Change how I
work.

## After saving

1. Save it; provision (Phase 5); report per practice.
2. `empirica finding-log` with what was set up: the tier, N practices provisioned, the preset if one was used.
3. Say how to change it: re-run `/ewm-interview`, or edit the YAML; it is theirs.
