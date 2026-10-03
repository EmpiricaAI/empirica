# Statusline Reference

**Status:** AUTHORITATIVE
**Source:** `empirica/plugins/claude-code-integration/scripts/statusline_empirica.py`
**Audience:** End users and developers
**Last Updated:** 2026-10-03 (v1.14.6)

---

## Overview

The Empirica statusline renders your current epistemic state in the Claude Code status bar. It reads vectors and session state from the local SQLite DB on every render — no model API calls, no network. If you see nothing, the script didn't run; if you see `[empirica] OFF-RECORD`, it ran but sentinel is paused.

Five display modes are available. `compact` is the default and the one most users see; `expanded` is the detailed view.

---

## Display Modes

Claude Code has no built-in compact/expanded toggle, so the mode is chosen two ways. The file wins and is
read on **every render**, so a switch takes effect at once with no restart:

```bash
echo expanded > ~/.empirica/statusline_mode   # detailed view
echo compact  > ~/.empirica/statusline_mode   # back to the default
```

When the file is absent, empty or names no mode, `EMPIRICA_STATUS_MODE` is used; when that is unset too, the mode is `compact`.

| Mode | Sections | When to use |
|------|----------|-------------|
| `compact` (default) | practice, stage + confidence, goals/unknowns/assumptions, findings/decisions, learning, context, model, investigate or act | General use: one short line |
| `expanded` (`default` is its old name) | confidence + open counts + phase composite + K/C + Δ + ctx% | When you want the numbers |
| `basic` | confidence + threshold | Minimal — just the headline |
| `learning` | confidence + threshold + open counts + phase + all 5 key vectors + Δ | When focusing on vector evolution |
| `full` | `[project:ai@sid]` + goal progress + phase + all vectors + Δ | Deep debugging / handoff review |

```bash
export EMPIRICA_STATUS_MODE=learning
```

---

## Compact Mode — Element by Element

Example: ` empirica  │ CHECK 73% │ G2 U5 A3 F4/D1 │ learning good │ 41%ctx │ 🧠 Sonnet 5.5 - 🔍 investigate`

1. **Practice**: black text on a white background, the project name.
2. **Stage and confidence**: `PRE`, `CHECK`, `POST` or `TEST`, then the confidence below. `POST` is POSTFLIGHT submitted and
   not yet graded; `TEST` is the post-test having graded the transaction. `---` means no transaction yet.
3. **Counts**: `G` open goals, `U` unresolved unknowns, `A` unresolved assumptions (all for the project), and
   `F/D` findings and decisions logged **in this transaction**, since its PREFLIGHT. Project totals run to thousands
   and say nothing about the window you are in. `F0/D0` is grey: you have not logged anything yet.
4. **Learning**: `poor | average | good | great` from the **grounded calibration** of the last closed transaction (the gap between
   what you assessed and what the evidence showed; not self-reported). Bands on the mean gap: under 0.10 great, under 0.20
   good, under 0.30 average, otherwise poor. `pending` until the post-test has graded a transaction, and `unrated` when under
   30% of the vectors were reached by any evidence, because the score then means little. The bands are a first pass.
5. **Context used**, as in expanded mode.
6. **Model**, then **`investigate` or `act`**: `investigate` while the work is noetic (PREFLIGHT, or a CHECK that has not
   proceeded), `act` once it is praxic.

---

## Expanded Mode — Segment-by-Segment

Example: `⚡83% ↕70% │ 🎯0 ❓0 │ POST ⚙82% │ K:80% C:85% │ Δ ✓ │ 58%ctx`

The `│` is a visual separator. Everything else encodes state.

### 1. Confidence — `⚡83%`

Weighted composite of your epistemic vectors:

```
confidence = 0.40 · know
           + 0.30 · (1 − uncertainty)
           + 0.20 · context
           + 0.10 · completion
```

Tiered emoji maps to the value:

| Emoji | Range | Color |
|-------|-------|-------|
| ⚡ | ≥ 75% | bright green |
| 💡 | 50–74% | green |
| 💫 | 35–49% | yellow |
| 🌑 | < 35% | red |

### 2. Dynamic CHECK Threshold — `↕70%`

The Brier-calibrated **know** threshold the Sentinel requires for auto-proceed past CHECK. Since 1.14 it is computed on the same basis the gate enforces: the current practitioner model's own calibration within the practice, falling back to the practice when that model has too few points. Arrow color signals **calibration health** (how much the Sentinel trusts your self-assessment):

| Color | Threshold inflation | Meaning |
|-------|--------------------|---------|
| green | ≤ 0.03 | Well-calibrated — threshold at baseline |
| yellow | ≤ 0.10 | Moderate miscalibration detected |
| red | > 0.10 | Significant miscalibration — Sentinel raises the bar |
| gray | — | Static fallback — neither the model nor the practice has enough calibrated transactions yet |

The threshold rises as your predicted confidence diverges from actual outcomes. It falls back as calibration improves. **The number you see is what you need to hit in PREFLIGHT know to skip CHECK.**

### 3. Open Counts — `🎯0 ❓0`

`🎯N` = open goals. `❓N` = open unknowns. Color scales with count (green 0 → yellow moderate → red high).

If goal-linked blockers exist: `❓total/blockers` (e.g., `❓119/70` = 119 unresolved, 70 blocking goals).

### 4. Transaction Phase — `POST`

Current phase in the epistemic transaction:

| Label | Phase |
|-------|-------|
| `PRE` | PREFLIGHT — transaction opened, awaiting CHECK |
| `CHK` | CHECK — readiness gate |
| `POST` | POSTFLIGHT — transaction closed |

### 5. Phase Composite — `⚙82%`

Vector composite for the current phase. The emoji indicates work phase:

| Emoji | Phase | Vectors averaged |
|-------|-------|------------------|
| 🔍 | noetic (investigating) | clarity, coherence, signal, density |
| ⚙ | praxic (acting) | state, change, completion, impact |
| — (at CHECK) | check-readiness gate | know, context, clarity, coherence, signal, density |

Color by value: green ≥ 75%, yellow ≥ 50%, red < 50%.

CHECK with a gate decision appends a transition indicator:
- `→` (green) — proceed was granted
- `…` (yellow) — investigate — more noetic work required

Example: `CHK 🔍82%→` means CHECK passed proceeding into praxic.

### 6. Raw Vectors — `K:80% C:85%`

Two of the 13 epistemic vectors shown in-line: `K` = know, `C` = context. These are your raw PREFLIGHT/CHECK values, not the composite. Color matches the phase-composite scheme.

In `learning` mode this expands to all five key vectors (`know`, `uncertainty`, `context`, `clarity`, `completion`).

### 7. POSTFLIGHT Deltas — `Δ ✓`

Only shown on POSTFLIGHT. Single-symbol summary of learning deltas across all vectors:

| Symbol | Meaning | Net delta |
|--------|---------|-----------|
| `✓` (green) | Net positive learning | > +0.05 |
| `△` (white) | Neutral — no meaningful change | −0.05 to +0.05 |
| `⚠` (red) | Net negative — check what regressed | < −0.05 |

Sign convention: for `uncertainty`, *lower* is better (counted as positive delta). All other vectors: higher is better.

### 8. Context Window — `58%ctx`

Claude Code context window usage, passed via stdin. Color: green < 50%, yellow 50–80%, red ≥ 80%.

Useful for deciding when to compact. The statusline also persists this percentage to `~/.empirica/context_usage.json` so UserPromptSubmit hooks can read it (hooks don't receive `context_window` directly).

---

## Edge States

When there's no normal session to display, the statusline shows one of these:

| Display | Meaning |
|---------|---------|
| `[empirica] OFF-RECORD` | Sentinel paused. Reads per-instance file `~/.empirica/sentinel_paused_{instance_id}` first (written by TUI's `P sent` button or `empirica sentinel pause --instance ID`); falls back to global `~/.empirica/sentinel_paused`. Measurements not being taken. |
| `[empirica] OFF-RECORD (Nm)` | Same, with time since pause (mtime-based) |
| `[no project]` | No `.empirica/project.yaml` found — not in an Empirica project |
| `[project:inactive]` | In a project, but no active session (`empirica session-create` hasn't run) |

If you see **nothing at all**, the script didn't run. Check Claude Code statusline settings, or run the script manually:

```bash
python3 ~/.claude/plugins/local/empirica/scripts/statusline_empirica.py < /dev/null
```

---

## Extensions

External packages can inject their own labels. Write a JSON file to `~/.empirica/statusline_ext/<name>.json`:

```json
{"label": "WS:4", "color": "cyan"}
```

The statusline reads every `*.json` in that directory and appends the labels (cyan by default) to the header. This is how `empirica-workspace` adds workspace counts, for example.

---

## Full Mode Example

```
[empirica:claude-code@3d0f] auth-f (2/5) ⚡83% ↕70% │ 🎯1 ❓3 │ POST ⚙82% │ K:80% U:20% C:85% D:75% Co:70% │ Δ ✓
```

- `[project:ai@sid]` — project label, AI ID, 4-char session ID prefix
- `auth-f (2/5)` — active goal (truncated to 12 chars) with task progress
- All vectors shown with 2-letter labels (`K`, `U`, `C`, `D`, `Co`, …)

---

## Full Mode Glyphs (Legacy Moon Phases)

In some legacy paths and debug output you may see moon-phase confidence emojis from the shared `empirica/core/signaling.py` module (`🌕 🌖 🌗 🌘 🌑`). These map roughly to the ⚡/💡/💫/🌑 tiers in the default statusline. The default mode uses the tiered power-emoji variant because it's more familiar; moon phases are retained in `full` for compatibility with older workflow docs.

---

## Environment Variables

| Var | Values | Default | Effect |
|-----|--------|---------|--------|
| `EMPIRICA_STATUS_MODE` | `compact` \| `expanded` \| `basic` \| `learning` \| `full` (`default` = `expanded`) | `compact` | Mode selector; `~/.empirica/statusline_mode` overrides it |
| `EMPIRICA_AI_ID` | any string | `claude-code` | Which AI's session to render |
| `EMPIRICA_SIGNALING_LEVEL` | `basic` \| `default` \| `full` | `default` | Signaling module verbosity |

---

## Common Questions

**"Why does ↕ show 70% when my know is at 85%?"**
The threshold (↕) is what the Sentinel requires, not your current value. 85% know against 70% threshold means you're above the bar and can auto-proceed past CHECK.

**"Why is `Δ` missing from my statusline?"**
`Δ` only renders on POSTFLIGHT (when deltas are computed). During PREFLIGHT / CHECK there's no learning delta to show yet.

**"Why does the phase composite differ from confidence?"**
Confidence is a weighted global score across 4 vectors. Phase composite averages a *different* subset per phase — noetic (clarity/coherence/signal/density) or praxic (state/change/completion/impact). They measure different things on purpose.

**"I see `OFF-RECORD` — how do I turn the sentinel back on?"**

```bash
empirica sentinel resume                  # this instance
empirica sentinel resume --instance <ID>  # specific instance
empirica sentinel resume --global         # clear global flag
```

Or from the cockpit TUI: select the instance, press `p`. The statusline
checks the per-instance file (`~/.empirica/sentinel_paused_{instance_id}`)
first, then the global file (`~/.empirica/sentinel_paused`); the verbs
above remove the right one.

**"Can I customize the glyphs?"**
Not via config currently — emoji and colors are hard-coded in `statusline_empirica.py`. Patches welcome.

---

## See Also

- [Sentinel Gate Reference](SENTINEL_GATE_REFERENCE.md) — the hook that enforces the CHECK gate referenced by the threshold display
- [Session Resolver API](SESSION_RESOLVER_API.md) — how the statusline resolves the current session
- [Environment Variables](ENVIRONMENT_VARIABLES.md) — all Empirica env vars in one place
