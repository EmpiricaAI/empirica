# Statusline Reference

**Status:** AUTHORITATIVE
**Source:** `empirica/plugins/claude-code-integration/scripts/statusline_empirica.py`
**Audience:** End users and developers

---

## Overview

The statusline renders the state of your current epistemic transaction in the Claude Code status bar. It runs on every render, reads vectors, goals and artifacts from the project's local SQLite store (`.empirica/sessions/sessions.db`), and makes no model call and no network call.

`empirica setup` adds a `statusLine` entry to `~/.claude/settings.json` when none exists (`type: command`, running `<python> <plugin dir>/scripts/statusline_empirica.py`); an existing entry is left alone. Claude Code pipes a JSON document to the script's stdin (context window, model, session id), which the script reads with a 0.1 second timeout.

The default view is `compact`, one short line:

```
 empirica  │ CHECK 73% │ G3 U5 A2 F4/D1 │ Δ … │ 41%ctx │ 🔨 act - 🧠 Sonnet 5.5
```

The five modes and every other output are below. The renderings in this document were produced by calling the script's own formatting functions (ANSI colour removed).

---

## Display modes

Claude Code has no compact/expanded toggle, so the mode is chosen two ways. The file wins and is read on **every render**, so a switch takes effect at once with no restart:

```bash
echo expanded > ~/.empirica/statusline_mode   # detailed view
echo compact  > ~/.empirica/statusline_mode   # back to the default
```

When the file is absent, empty, or names no mode, `EMPIRICA_STATUS_MODE` is used; when that is unset or invalid too, the mode is `compact`. `default` is accepted as the old name of `expanded`.

| Mode | Shows | Use it for |
|---|---|---|
| `compact` (default) | practice, stage and confidence, open counts, findings/decisions in this transaction, learning mark, context used, `investigate` or `act`, model | Everyday use |
| `expanded` | confidence, open goals and unknowns, phase composite, two raw vectors, POSTFLIGHT delta, context used | When you want the numbers |
| `basic` | confidence only | A minimal headline |
| `learning` | confidence, open counts, phase name, five vectors, POSTFLIGHT delta | Watching vectors move |
| `full` | `[project:ai@session]`, active goal with progress, phase name, seven vectors, POSTFLIGHT delta | Debugging, handoff review |

Every mode except `compact` appends the model tag after a three-space gap; `compact` carries it inline. `EMPIRICA_STATUS_MODEL=0` (or `false`, `off`) hides it.

---

## Compact mode, element by element

Through one transaction the same sample project renders as:

```
 empirica  │ PRE 55% │ G3 U5 A2 F0/D0 │ Δ … │ 41%ctx │ 🔍 investigate - 🧠 Sonnet 5.5
 empirica  │ CHECK 73% │ G3 U5 A2 F2/D1 │ Δ … │ 41%ctx │ 🔍 investigate - 🧠 Sonnet 5.5
 empirica  │ CHECK 73% │ G3 U5 A2 F4/D1 │ Δ … │ 41%ctx │ 🔨 act - 🧠 Sonnet 5.5
 empirica  │ POST 82% │ G3 U5 A2 F4/D1 │ Δ … │ 41%ctx │ 🔨 act - 🧠 Sonnet 5.5
 empirica  │ TEST 82% │ G3 U5 A2 F4/D1 │ Δ ✓ │ 41%ctx │ 🔨 act - 🧠 Sonnet 5.5
```

1. **Practice.** The project name in black on white, truncated to 18 characters plus `..` past 20.
2. **Stage and confidence.** `PRE`, `CHECK`, `POST` or `TEST`, then the confidence composite (formula under Expanded mode, colour by value, no emoji). `POST` is POSTFLIGHT submitted and not yet graded; `TEST` is the post-test having graded the transaction. `---` means no vectors yet.
3. **Counts.** `G` open goals, `U` unresolved unknowns, `A` unresolved (`unverified`) assumptions, all for the project. `F/D` are findings and decisions logged **in this transaction** since its PREFLIGHT, not project totals, which run to thousands and say nothing about the window you are in. `F0/D0` is grey: nothing logged yet.
4. **Learning.** `Δ` and a mark from the **grounded calibration** of the transaction (the gap between what you assessed and what the evidence showed; not self-reported, so reporting higher numbers cannot raise it). The mark appears only once the post-test has graded a transaction, which happens after POSTFLIGHT, so **while a transaction is open it is `…`**. The bands, on the mean gap: 🔥 under 0.10, ✓ under 0.20, `-` under 0.30, ✗ at or above 0.30. `?` replaces the mark when under 30% of the vectors were reached by any evidence, because the score then means little. The bands are a first pass.
5. **Context used**, coloured green below 50%, yellow to 80%, red above.
6. **`investigate` or `act`, then the model.** `act` once the work is praxic: a CHECK that proceeded, or POSTFLIGHT. `investigate` for PREFLIGHT, a CHECK that has not proceeded, or no phase.

After POSTFLIGHT closes the transaction file, the counts in 3 and the mark in 4 keep describing the transaction that just closed (the one of the most recent reflex) until the next PREFLIGHT opens a new one. The four learning marks:

```
 empirica  │ TEST 82% │ G3 U5 A2 F4/D1 │ Δ 🔥 │ ...      (gap 0.05)
 empirica  │ TEST 82% │ G3 U5 A2 F4/D1 │ Δ ✓ │ ...      (gap 0.12)
 empirica  │ TEST 82% │ G3 U5 A2 F4/D1 │ Δ - │ ...      (gap 0.25)
 empirica  │ TEST 82% │ G3 U5 A2 F4/D1 │ Δ ✗ │ ...      (gap 0.35)
```

---

## Expanded mode, segment by segment

```
[empirica] 💡55% │ 🎯3 ❓5/2 │ PRE 🔍63% │ K:60% C:70% │ 41%ctx   🧠 Sonnet 5.5
[empirica] 💡73% │ 🎯3 ❓5/2 │ CHK 🔍77%… │ K:80% C:85% │ 41%ctx   🧠 Sonnet 5.5
[empirica] 💡73% │ 🎯3 ❓5/2 │ CHK 🔨77%→ │ K:80% C:85% │ 41%ctx   🧠 Sonnet 5.5
[empirica] ⚡82% │ 🎯3 ❓5/2 │ POST 🔨78% │ S:80% Δ:75% │ Δ ✓ │ 41%ctx   🧠 Sonnet 5.5
```

`│` is a visual separator; everything else encodes state. The label is the project name (green).

### 1. Confidence: `⚡82%`

A weighted composite of the raw vectors:

```
confidence = 0.40 · know + 0.30 · (1 − uncertainty) + 0.20 · context + 0.10 · completion
```

| Emoji | Range | Colour |
|---|---|---|
| ⚡ | 75% and up | bright green |
| 💡 | 50-74% | green |
| 💫 | 35-49% | yellow |
| 🌑 | below 35% | red |

### 2. Open counts: `🎯3 ❓5/2`

`🎯N` open goals, `❓N` unresolved unknowns. If some unknowns are linked to goals (blockers), `❓total/blockers`: `❓5/2` is 5 unresolved, 2 blocking goals. Goals are green at 0, yellow at 1-2, cyan above; unknowns are coloured by their blocker count (green at 0, yellow to 5, cyan above). Red is never used. With no count data the segment is a grey `--`.

### 3. Transaction phase: `PRE`, `CHK`, `POST`

PREFLIGHT, CHECK, POSTFLIGHT. (Compact spells CHECK in full and adds `TEST`.)

### 4. Phase composite: `🔨78%`

The mean of a vector subset chosen by phase, with the emoji for the work mode:

| Emoji | Mode | Vectors averaged |
|---|---|---|
| 🔍 | noetic | clarity, coherence, signal, density |
| 🔨 | praxic | state, change, completion, impact |

At CHECK the composite is the readiness mean (know, context, clarity, coherence, signal, density) whatever the decision, and the emoji follows the gate decision: 🔨 once `proceed`, 🔍 otherwise. A CHECK with a decision appends `→` (green, proceed) or `…` (yellow, investigate). Colour by value: green at 75% and up, yellow at 50%, red below.

### 5. Raw vectors: `K:80% C:85%`

`K` know and `C` context: the submitted values, not the composite. When the composite is the praxic one (POSTFLIGHT) the pair switches to `S` state and `Δ` change, the vectors that composite averages. Here `Δ:75%` is the **change vector**; the separate `Δ ✓` is the learning summary below.

### 6. POSTFLIGHT delta: `Δ ✓`

Only on POSTFLIGHT, and only when the deltas against PREFLIGHT are large enough to record (a vector must move by 0.05 or more). One symbol for the net across vectors, where a drop in `uncertainty` counts as positive:

| Symbol | Meaning | Net delta |
|---|---|---|
| `✓` green | net learning | above +0.05 |
| `△` white | neutral | −0.05 to +0.05 |
| `⚠` red | net regression | below −0.05 |

### 7. Context window: `41%ctx`

Claude Code's context usage from stdin. The percentage is also written to `~/.empirica/context_usage.json` and to `~/.empirica/context_usage_{instance}.json` so hooks and the cockpit can read it (hooks do not receive `context_window`). `EMPIRICA_CTX_METER=1` (or `true`, `bar`) renders a bar instead: `[####------] 41%`.

---

## The other modes

```
basic     [empirica] 💡73%   🧠 Sonnet 5.5
learning  [empirica] ⚡82% │ 🎯3 ❓5/2 │ POSTFLIGHT │ K:80% U:20% C:85% L:75% ✓:90% │ Δ ✓   🧠 Sonnet 5.5
full      [empirica:claude-code@3d0f] │ auth-fix-t.. ██░░░░ 40% (2/5) │ POSTFLIGHT │ K:80% U:20% C:85% L:75% E:80% ✓:90% I:70% │ Δ ✓   🧠 Sonnet 5.5
```

- `learning` spells the phase out and shows `K` know, `U` uncertainty, `C` context, `L` clarity, `✓` completion.
- `full` shows `[project:ai_id@first 4 characters of the session id]`, the active goal (name cut to 12 characters, a progress bar, `done/total` tasks, or a grey `no goal`), the phase spelled out, and seven vectors: the five above plus `E` engagement and `I` impact. It has no confidence glyph.

---

## Other outputs and edge states

| Output | Meaning |
|---|---|
| `[empirica] OFF-RECORD` or `OFF-RECORD (12m)` | The Sentinel is paused. The per-instance file `~/.empirica/sentinel_paused_{instance_id}` is checked first, then the global `~/.empirica/sentinel_paused`. The age comes from the file's mtime (or `paused_at` in an old JSON file). No measurement is being taken |
| `[no project]` | No project path could be resolved: no mapping for this instance or Claude session, no `EMPIRICA_PROJECT_PATH`, no TTY session, no git-root store, and no `.empirica/sessions/sessions.db` at or above the working directory |
| `[<project>:inactive]` | A project was found but it holds no active session for this `ai_id` (`empirica session-create` has not run). `ai_id` is `EMPIRICA_AI_ID`, else `project.yaml`'s `ai_id`, else the directory name, else `claude-code` |
| `[empirica:error]` | The script raised; the exception is appended to `statusline.log` in the empirica root |
| nothing at all | The script did not run, or the instance is **headless**: with no terminal identity (no `TMUX_PANE`, `WINDOWID`, TTY and so on) the statusline prints nothing. `EMPIRICA_HEADLESS=false` overrides the detection, `true` forces it |

To run the script by hand:

```bash
python3 ~/.claude/plugins/local/empirica/scripts/statusline_empirica.py < /dev/null
```

Two machine-readable forms bypass the layouts: `--json` (or `EMPIRICA_STATUS_JSON=true`) prints project, session, phase, vectors, deltas, confidence, open goals and unknowns, gate decision and extensions as JSON for dashboards; `--tmux` (or `EMPIRICA_STATUS_TMUX=true`) prints a short `E:⚡80% CHK` for a tmux status bar.

---

## Extensions

External packages can add labels by writing JSON to `~/.empirica/statusline_ext/<name>.json`:

```json
{"label": "WS:4", "color": "cyan"}
```

Every `*.json` in that directory is read and its label (cyan by default) appended to the header of `expanded`, `basic` and `learning`. `compact` and `full` do not render them; `--json` lists them. This is how `empirica-workspace` adds workspace counts.

---

## Environment variables

| Variable | Values | Default | Effect |
|---|---|---|---|
| `EMPIRICA_STATUS_MODE` | `compact`, `expanded`, `basic`, `learning`, `full` (`default` = `expanded`) | `compact` | Mode, when `~/.empirica/statusline_mode` does not name one |
| `EMPIRICA_AI_ID` | any string | `project.yaml` `ai_id`, then the project directory name, then `claude-code` | Which practice's session to render |
| `EMPIRICA_STATUS_MODEL` | `0`, `false`, `off` | shown | Hides the `🧠 model` tag |
| `EMPIRICA_CTX_METER` | `1`, `true`, `bar` | off | Context as a bar instead of `%ctx` |
| `EMPIRICA_STATUS_JSON`, `EMPIRICA_STATUS_TMUX` | `true` | off | JSON or tmux output instead of a layout |
| `EMPIRICA_HEADLESS` | `true`, `false` | auto-detected | Force or suppress headless mode (no statusline) |

---

## Common questions

**Why is the `Δ` mark `…`?** The post-test grades a transaction after POSTFLIGHT. While one is open, or just closed and not yet graded, there is nothing to show.

**Why does the phase composite differ from confidence?** Confidence is a global score over four vectors. The composite averages a different subset per phase. They measure different things on purpose.

**I see `OFF-RECORD`. How do I turn the Sentinel back on?**

```bash
empirica sentinel resume                  # this instance
empirica sentinel resume --instance <ID>  # a specific instance
empirica sentinel resume --global         # clear the global flag
```

or `empirica on`. Both remove the pause file the statusline is reading.

**Can I customise the glyphs?** Not through configuration. Emoji and colours are in `statusline_empirica.py`.

---

## See also

- [Sentinel Gate Reference](SENTINEL_GATE_REFERENCE.md): the hook behind the pause state
- [Session Resolver API](SESSION_RESOLVER_API.md): how the current session is resolved
- [Environment Variables](ENVIRONMENT_VARIABLES.md): all Empirica env vars in one place
