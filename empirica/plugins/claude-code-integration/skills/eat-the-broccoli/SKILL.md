---
name: eat-the-broccoli
description: "Use when the user says '/eat-the-broccoli', 'eat the broccoli', 'full quality sweep', 'pre-release audit', 'run the deep tests', or asks to hunt for gaps / membrane misses / stubs / dead code / silent failures / the bugs that pass tests but are still broken. A tiered quality-and-pattern audit: deterministic tooling (lint, types, tests, deps, dead-code, silent-failure) PLUS a learned-pattern hunt for the hard, judgment-requiring failure classes that tools can't catch. Works in any repo or stack. Levels: quick / standard / deep. Scope: changed / module / repo."
version: 2.1.0
---

<!-- Vendored from https://github.com/EmpiricaAI/broccoli — edit upstream, not here. -->

# Eat the Broccoli 🥦

The thorough quality sweep you run before a release, after a refactor, or when
something just *smells* off. Two halves:

1. **Deterministic tooling** — lint, types, tests, dependency CVEs, dead code,
   silent failures. Catches the *known* categories.
2. **The pattern hunt** — a checklist of the *hard* failure classes that pass
   every test and still ship broken: races, stale caches, silent fallbacks,
   null-masking, deploy-staleness. Tools can't catch these; only a trained eye
   plus a *"is this by design?"* check can.

> **Portable by design.** The deterministic half is tool-agnostic — wire in your
> stack's linter / typechecker / scanner. The pattern hunt needs no tooling at
> all; it's a structured way of looking.

---

## Two dials: depth × scope

| Depth | digs into | Scope | covers |
|---|---|---|---|
| **quick** | lint + fast tests + dep-audit | **changed** | the diff vs your base branch |
| **standard** | + dead-code, silent-failure, contract guards, pattern hunt | **module** | one subsystem / path |
| **deep** | + full pattern hunt, docs/contract accuracy, multi-pass | **repo** | everything |

Pick any combination — `quick × changed` is a pre-commit reflex; `deep × repo`
is a pre-release gate. **State the level + scope you chose, and say what you
skipped** — silent truncation reads as "covered everything."

> **Forks & monorepos:** when most of the tree is upstream/vendored, `repo`
> scope drowns in code you don't own. Default to `changed` or your owned
> packages — here the scope dial is load-bearing, not optional.

---

## Phase 1 — Deterministic tooling

Run your stack's equivalent of each. Skip what you don't have; the sweep still
works.

| Dimension | Tools |
|---|---|
| Lint / style | ruff, eslint, golangci-lint, clippy |
| Types | pyright, tsc, mypy, `cargo check` |
| Tests | pytest, jest, go test, cargo test |
| Dependency CVEs | pip-audit, npm audit, cargo-audit |
| Dead code | vulture, ts-prune, cargo-machete |
| Silent failures | bare `except:` / empty `catch {}` / discarded `Result` |
| Contract guards | your CLI / API contract tests |

### Tooling by language

The dimensions map to concrete tools. Two reference stacks:

**Python**
| Dimension | Tool | Install / run |
|---|---|---|
| Lint + silent-failure | ruff | `pip install ruff` → `ruff check` (silent: `--select S110,S112,BLE001`) |
| Types | pyright | `pip install pyright` → `pyright` |
| Tests | pytest | `pip install pytest` → `pytest -q` |
| Dep CVEs | pip-audit | `pip install pip-audit` → `pip-audit` |
| Dead code | vulture | `pip install vulture` → `vulture src/ --min-confidence 80` |
| Complexity | radon | `pip install radon` → `radon cc src/ --min C` |
| SAST | semgrep | `pip install semgrep` → `semgrep --config auto` |
| Secrets | trufflehog | `trufflehog git file://.` |

**Rust**
| Dimension | Tool | Install / run |
|---|---|---|
| Lint | clippy | `rustup component add clippy` → `cargo clippy -- -D warnings` |
| Format | rustfmt | `cargo fmt --check` |
| Types / build | rustc | `cargo check` |
| Tests | cargo&nbsp;test / nextest | `cargo test` (or `cargo install cargo-nextest` → `cargo nextest run`) |
| Dep CVEs + licenses | cargo-audit, cargo-deny | `cargo install cargo-audit cargo-deny` → `cargo audit`, `cargo deny check` |
| Unused deps / dead code | cargo-machete, rustc `dead_code` | `cargo install cargo-machete` → `cargo machete` (`dead_code` lint is on by default) |
| Silent failures | clippy | `-W clippy::unwrap_used -W clippy::let_underscore_must_use` + hunt discarded Results: `let _ = fallible()`, `.ok()`, `.unwrap_or_default()` |
| Unsafe audit | cargo-geiger | `cargo install cargo-geiger` → `cargo geiger` |

> The **pattern hunt below is language-agnostic** — it ports verbatim across any
> stack.

**Reading silent failures:** a truly-silent swallow (`except: pass`, empty
`catch {}`, `let _ = fallible()`) is the dangerous one. A broad catch that *logs
or re-raises* is often deliberate degradation — flag the **new** ones, and any
with no log and no re-raise, not the absolute count.

---

## Phase 2 — The pattern hunt 🥦 (the part that earns the name)

Tools find known categories. These are the **structural** failures that pass
tests and still ship broken. Each is a place where **broken and by-design look
identical** — so each row carries the disambiguator that tells them apart.

**The one meta-question:** *"This looks intentional — is it actually?"* When you
find a smell, check the intent (a comment, a test, a doc, or ask). When it
resolves to "yes, by design," **record that verdict** (an inline annotation or a
`.broccoli-accept` line — see below) so you don't re-litigate it next sweep.

*Worked example.* You spot a function returning `[]` on a DB timeout
(**fallback-masks-primary**). Check the intent: does it log or raise? It logs
nothing → **❌ broken** — callers can't tell "no rows" from "DB down." If instead
it logged a warning and returned `[]` as a *documented* degraded mode →
**✅ by design**: record it with the reason, move on.

### A. State & timing
| Smell | ✅ by design if | ❌ broken if |
|---|---|---|
| **Race / shared mutable state** — >1 writer to one key/file | single-writer guaranteed, or the key carries the writer's durable id | two writers share a proxy key → last-write-wins clobber |
| **Stale cache** — value resolved once, reused | the value is immutable for that lifetime | it's ephemeral/locational but frozen at init (location, clock, "current X") |
| **Idempotency miss** — a retried / redelivered op | dedup-key / set-semantics make replay harmless | it increments / appends / re-sends on replay |
| **Off-by-one window** — a since/until, threshold, range | inclusive/exclusive matches intent | the event you need lands exactly on the excluded edge |
| **Coupled-lifecycle orphan** — two things that must live/die together | bound, or a reaper cleans orphans | orphans pile up silently (a handle without its resource; a record opened, never closed) |

### B. Failure visibility
| Smell | ✅ by design if | ❌ broken if |
|---|---|---|
| **Empty/null masking** — a field empty because something broke upstream | the empty has a *contract* (declared nullable) | a count no longer matches its list; a `.get(k, default)` ate a missing key |
| **Default masks dropped intent** — a param with a default | the default is a real sensible value | it silently absorbs a value that *was* supplied but got dropped |
| **Fallback masks primary failure** — a degraded path | it logs/flags that it engaged | "it works" actually means "fallback works, primary silently dead" |
| **Partial success as success** — a multi-step op, one rollup status | rollup is AND-of-all + per-item surfaced | a mid-step failure is swallowed by the final OK |
| **Unfalsifiable success** — no distinct signal for worked-vs-failed | success and failure produce *different* legible output | both look the same (empty output, silence). *Ask: if this failed, could I tell?* |

### C. Boundaries & contracts
| Smell | ✅ by design if | ❌ broken if |
|---|---|---|
| **Classifier blind spot / membrane miss** — a matcher on crossing data | it sees through wrappers / chains / quotes / encodings | a wrapped or chained variant slips a start-anchored match |
| **Encoding / quoting mangle** — data crossing CLI↔shell, JSON↔DB, wire↔local | the boundary escapes / validates explicitly | it assumes clean input (quotes, newlines, unicode, `"0.7"` vs `0.7`) |
| **Schema drift** — code assumes a shape the migration didn't produce | the unused shape is intentionally deprecated-but-kept | the code path is silently dead against the real schema |
| **Trust-the-input** — consuming upstream data unvalidated | validated at the boundary | it assumes well-formed and NoneTypes three calls later |
| **Two-sources-of-truth drift** — a copy of a load-bearing thing | one is generated from the other | both are hand-maintained and have diverged |
| **Semantic drift** — one word, different meanings across components | one canonical definition, referenced | each component quietly means its own thing (`id`, `session`, `scope`, "done") |
| **Decision downgraded across a boundary** — a deny/soft decision crossing into a consumer with a *narrower* vocabulary | it degrades to the **floor** (deny / fail-closed) | it silently downgrades to *allow* — the three below |
| ↳ *exit-code / status mismatch* — a host maps a subprocess code to a decision, defaulting on any unrecognized value | the default is the safe one (fail-closed) | unsafe default — a *crashed* gate fails open (e.g. "anything ≠ 2 ⇒ allow") |
| ↳ *reason-less deny dropped* — a consumer drops a deny when a required field (reason/message) is empty | *(never by design)* | an empty-reason deny silently becomes allow |
| ↳ *advisory unsupported downstream* — an upstream soft decision (ask/warn) the consumer can't express | it degrades to the floor (deny) | it silently downgrades to allow (no middle ⇒ must fail safe) |

### D. Environment & control flow
| Smell | ✅ by design if | ❌ broken if |
|---|---|---|
| **Deploy-staleness** — installed/copied artifact vs source | hash/mtime match, or a single source of truth | the box runs old code while source is "fixed" (the #1 recurring root cause) |
| **Config/env divergence** — behavior depends on env / version / run-context | the dependency is declared + checked with a clear error | assumed present (works interactively, absent in CI / cron / a peer's box) |
| **Authority on the wrong field** — a gate / filter / recipient check | keyed on the source of truth | keyed on a proxy that *usually* agrees |
| **Gate gates its own escape** — a guard blocking its own clear-path | the recovery action is always-open *before* the gate | the verb that would clear the deny is itself denied |
| **Unrecoverable gate** — a deny with a "do X first" message | doing X actually satisfies it | the satisfaction window closed before X can run |
| **Dead branch by construction** — a path an earlier check already decided | intentional belt-and-suspenders | genuinely unreachable (shadowed by a prior return) |

> **This table is living.** When a new class of issue bites you, add a row with
> its disambiguator — every incident becomes a permanent future check. Found one
> we're missing? **PR it** (see Contributing).

---

## Phase 3 — Triage, verdict, record

1. **Real issue** → log it (an issue, a TODO, your tracker).
2. **Confirmed by-design** → **record the verdict** (an inline annotation or a `.broccoli-accept` line) so the next sweep skips it.
3. **Rank by blast radius, then cut.** Sort findings by the damage the failure would do, not by how many you found — a sweep with 8 prioritized findings gets acted on; one with 30 gets skimmed and ignored. Calibrate severity to the *stated* context: a missing backup is a 🟢 note for a scratch script and a 🔴 blocker for a billing path. Never grade an MVP against an enterprise checklist — that's noise wearing a badge.
4. **Roll up a verdict:** 🟢 **GREEN** (ship) · 🟡 **YELLOW** (ship + logged follow-ups) · 🔴 **RED** (blockers — name them).
5. Re-runs are idempotent: track counts over time. A *rising* silent-failure / debt count is the signal, not the absolute number.

### The `.broccoli-accept` file

One confirmed by-design finding per line — a `.gitignore` for false alarms, so
the next sweep stays quiet on what you've already judged:

```
# pattern:location — why it's intentional (and what would reverse it)
fallback-masks-primary:db/cache.py:fetch — logged degraded mode; reverse if callers start trusting []
blind-except:sync/git_notes.py — non-fatal best-effort write; errors visible upstream
```

Keep the *why*. A verdict without a reason is just a mute button — and the next
person (or the next you) can't tell a real judgment from a silenced alarm.

---

## Contributing

The pattern hunt is meant to **grow across languages and harnesses** — that's the
whole point. Hit a failure class that isn't here? Open a PR with a row:

```
| **Name** — the smell | ✅ by design if … | ❌ broken if … |
```

Real war stories make the best rows. The catalog gets sharper every time someone
adds the bug that just bit them.

---

## Why "broccoli"?

Because it's the work you know you should do and skip anyway. This makes it a
single command, gives the boring-but-load-bearing checks a place to live, and —
once a verdict is recorded — means you never chew the same stalk twice. 🥦

---

*Want the deterministic checks bundled into one command, and by-design verdicts
recorded for you so they compound across a team? See
[INTEGRATIONS.md](https://github.com/EmpiricaAI/broccoli/blob/master/INTEGRATIONS.md).*
