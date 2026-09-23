# Upgrading to Empirica 1.14

This guide covers the 1.13.x → 1.14 jump. **One behaviour change can refuse work
that used to proceed**, and it is deliberate: a transaction opened against
another practice's session is now refused instead of writing into the wrong
store. Everything else is additive.

---

## Quick upgrade

```bash
pip install --upgrade empirica empirica-mcp
empirica setup-claude-code --force          # refresh hooks + plugin skills
empirica doctor                             # green = ready
```

If you run a persistent listener as an OS service, restart it so it picks up the
new code:

```bash
systemctl --user restart empirica-listener             # Linux
launchctl kickstart -k gui/$UID/com.empirica.listener  # macOS
```

**Upgrade the CLI before, or with, the plugin.** The plugin directory is
user-global and shared by every practice on the box, while each practice
upgrades its own `empirica`. A plugin newer than the CLI is routine, and 1.14's
session hook passes a flag (`--record-build`) that a 1.13 CLI rejects. The hook
retries without it, so nothing is lost — but the build facts below stay empty
until the CLI catches up.

---

## Behaviour change — a session belongs to one practice

**What changed.** `preflight-submit` now refuses when the session it is given
belongs to a *different registered practice*, before writing anything:

```json
{
  "ok": false,
  "error": "Session '…' belongs to the practice 'empirica-cortex', not to this checkout…",
  "owning_practice": "empirica-cortex",
  "owning_store": "/…/empirica-cortex/.empirica/sessions/sessions.db"
}
```

**Why.** `sessions.db` lives inside each project's own `.empirica/`. A
transaction opened from one checkout against another practice's session writes
that practice's measurement into this store, where its own tooling will never
see it. It happened, and left an orphan row behind.

**What did not change.** A session that exists *nowhere* still only warns and
proceeds — that is a legitimate first transaction on a session created outside
the CLI. And if the practice registry cannot be read for any reason, the old
warning stands: the guard never refuses on a failure to check.

**If this refuses you:** run the command from that practice's checkout, or start
a session for the practice you are in with `empirica session-create`.

---

## New — falsifiers

A falsifier is the observation that would refute a belief, registered **before**
the evidence that tests it. It exists for a failure no confidence gate can see:
a true measurement asserted past the population it was taken over adjudicates
`held`, and the refutation usually arrives after the transaction has closed.

Register at PREFLIGHT or CHECK, beside `claims`:

```json
{
  "vectors": {"know": 0.8, "uncertainty": 0.2},
  "falsifiers": [{
    "statement": "any proposal still at eco_review after an ntfy_skipped_afk_off entry",
    "query": "sqlite3 …  SELECT id FROM proposals WHERE status='eco_review' …",
    "falsifies": "709881ce"
  }]
}
```

- `falsifies` names the artifact it tests — a finding, assumption, decision,
  dead_end, mistake or lesson. One with no parent, or a parent that does not
  resolve, is **refused and named**, not stored. An unknown asserts nothing, so
  it cannot be falsified.
- A `query` is optional and strongly preferred: an executable falsifier can be
  re-run by someone who does not know the belief.
- Every later PREFLIGHT lists the practice's open falsifiers.
- POSTFLIGHT adjudicates any open one:

```json
{"falsifiers": [{"id": "f701c90f", "state": "tripped", "tripped_by": "prop_…"}]}
```

`survived` **requires evidence** that the population was observed; without it
the verdict is recorded as `expired`, because nobody having looked is not the
same as the belief holding.

`empirica falsifier-list` prints them with counts by state.

---

## New — calibration follows the practitioner

CHECK's dynamic thresholds now read the **current model's own** calibration
trajectory within the practice, falling back to the practice's when that model
has too few points. The response says which basis it used:

```json
{"basis": {"calibration_ai_id": "empirica", "practitioner_model": "claude-opus-5",
           "calibration_basis": "practitioner", "practitioner_points": 8}}
```

Artifacts still accrue to the practice. Nothing to configure; rows logged before
the model was recorded count for the practice only.

---

## New — a seat records what it is running

A session records its own `version` and a content digest of the code it
imported, and the listener daemon forwards that unchanged rather than measuring
itself. Both version strings are kept, because they disagree in practice:

```json
{"build": {"source": "in_process", "version": "1.14.0", "dist_version": "1.13.51",
           "version_disagrees": true, "digest": "4b859388…"}}
```

`version` ships with the imported code, `dist_version` is what the environment's
metadata claims, and the digest is the authority over either — an editable
install can serve newer code than its metadata names.

`setup` and `plugin-sync` accept `--ai-id` for a caller that knows which
practice a deploy is for. Without it, the writer stamp records the operator
(`user@host`) and no practice name, because a fleet deploy is performed by an
operator and on a multi-practice box no practice name would be true.

---

## Fixed, worth knowing about

- **Artifacts larger than 128 KB never reached git notes.** Every note writer
  passed the body as one argument, which Linux caps, so the write failed and the
  artifact existed only in SQLite — where a rebuild from notes drops it. If you
  have logged very large artifacts, run `python3 scripts/backfill_git_notes.py`
  (dry run) and then `--apply`.
- **`doctor`'s notes/sqlite check overcounted** unstamped resolutions by
  reporting every resolved artifact that had a note, and the count could not
  fall after a repair. Re-run `empirica doctor --reconcile-notes` for a true
  plan.
- **`goals-list --output json`** now carries the truncation notice its human
  output printed, naming `--uncapped`.

---

## Housekeeping scripts

If your store predates the test-isolation fix, the suite may have written into
it. All three scripts dry-run by default and back up before applying:

```bash
python3 scripts/prune_orphan_reflexes.py --all-unregistered   # reflex rows
python3 scripts/prune_test_data.py                            # sessions, fixture projects, issues
python3 scripts/bind_store_sessions.py                        # sessions missing their project stamp
```

Older upgrade guides (1.9 – 1.13) remain in [docs/guides/](.).
