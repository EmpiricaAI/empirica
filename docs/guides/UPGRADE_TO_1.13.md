# Upgrading to Empirica 1.13

This guide covers the 1.12.x → 1.13.0 jump. **There are two breaking changes**, both
deliberate and both in the safe direction — a destructive verb no longer acts by
default, and a scheduled job no longer installs itself. Neither will break a
running system silently: the first refuses to delete, the second stops scheduling.

---

## Quick Upgrade

```bash
pip install --upgrade empirica empirica-mcp
empirica setup-claude-code --force          # Refresh hooks + plugin skills
empirica diagnose                            # Sanity check — green = ready
```

If you run a persistent listener as an OS service, restart it so it picks up the
new code:

```bash
systemctl --user restart empirica-listener   # Linux
launchctl kickstart -k gui/$UID/com.empirica.listener   # macOS
```

---

## Breaking change 1 — `delete-artifacts` previews by default

**What changed.** A bare `empirica delete-artifacts -` now **previews**. Pass
`--apply` to actually delete.

```bash
# before 1.13
empirica delete-artifacts -              # DELETED immediately
empirica delete-artifacts - --dry-run    # previewed

# 1.13 onward
empirica delete-artifacts -              # previews (dry_run: true)
empirica delete-artifacts - --apply      # deletes
empirica delete-artifacts - --dry-run    # still accepted, now a no-op
```

**Why.** The gardening skill, `docs/architecture/ARTIFACT_HYGIENE.md` and the
Empirica system prompt all documented dry-run-as-default and an `--apply` flag —
while the code had `--dry-run` as opt-in and deleted immediately. Anyone
following the documented "preview first, then apply" workflow destroyed
artifacts and learned so from the receipt afterwards. Deletion is the one lever
with no history to recover from, so the docs described the right design and the
code was the defect.

**Do you need to act?** Only if you script `delete-artifacts` non-interactively.
A script written against the documentation already expected preview; a script
written against the old behaviour now previews instead of deleting, reports
`"dry_run": true`, and destroys nothing. Add `--apply` where you meant it.

`--dry-run` is still accepted and does nothing, because it is the flag three
documents told people to pass — it must not error.

---

## Breaking change 2 — no cron loop is installed by default

**What changed.** No `kind: cron` loop auto-installs. In practice this means the
daily `message-cleanup` housekeeping loop is no longer scheduled on a fresh
practice, and **a new practice acquires no scheduled jobs at all**.

**Why.** A cron job is a standing scheduled process on your machine. It outlives
the session that created it and runs whether or not anyone is watching, so
installing one unasked is a side effect you did not consent to. Wake-on-event —
the persistent listener plus the SessionStart Monitor — is inert until something
real happens, and is the preferred trigger wherever the harness supports it.

The gate keys on `kind == "cron"` rather than on a per-entry flag, because the
entry that broke this rule was precisely the one that never set the flag.

**Do you need to act?** Only if you want the daily mesh-message cleanup. Opt in:

```bash
empirica loop register --name message-cleanup --kind cron \
  --cron "17 3 * * *" \
  --description "Daily cleanup of expired git-notes mesh messages"
```

**Consequence if you don't:** expired mesh messages are not pruned on that seat.
Nothing else changes.

Existing registered loops are untouched — this only affects what gets installed
automatically on a practice that has none.

See [`docs/architecture/TRIGGER_MODEL.md`](../architecture/TRIGGER_MODEL.md) for
the full model: what each trigger mechanism costs, when to reach for which, and
how the opt-in rule is enforced.

---

## What's new in 1.13

**Seven reporter-found defects fixed**, most of them one shape: an operation
that did nothing while reporting that it did something.

- **PREFLIGHT/CHECK retrieval was silently empty** unless `EMPIRICA_QDRANT_URL`
  was set — even with Qdrant running on the default port. Writes landed, reads
  returned nothing, and an empty result is indistinguishable from "nothing
  matched" (#388).
- **`calibration-report` bias corrections pointed the wrong way** on exactly the
  vectors where self-assessment sat above the evidence — it told an
  overconfident practitioner to assess higher. The same number is injected at
  session start as a pattern to internalize (#393).
- **`unknown-resolve` reported success for ids that do not exist**, and wrote a
  git note for the resolution that never happened (#390).
- **`message-read` never read the message** — it marked it read and returned
  that receipt (#391).
- **Mistake `prevention` came back empty, or as the word "None"** (#392).
- **A limited inbox returned an arbitrary subset, not the newest N** (#394).
- **Cron loops reported an invented next-fire time** — "every day at 09:00" came
  back as "in 15 minutes" (#396).

**The batch artifact verbs no longer mutate by unbounded prefix.**
`resolve-artifacts` issued `UPDATE ... WHERE id LIKE ?` with no LIMIT across six
branches, so a short id resolved *every* matching artifact while the receipt
counted one. All three verbs now resolve to exactly one id, and refuse a blank,
too-short, or ambiguous one.

**API reference docs are verified.** 43 documented functions did not exist; they
are removed, each affected file carries a generated-from-source **Verified API
surface**, and a test fails CI if a phantom is documented again.

Full detail in [`CHANGELOG.md`](../../CHANGELOG.md).

---

## Action items for upgraders

| If you… | Do this |
|---|---|
| script `delete-artifacts` | add `--apply` where deletion is intended |
| want daily mesh-message cleanup | `empirica loop register --name message-cleanup --kind cron --cron "17 3 * * *"` |
| rely on semantic doc search | run `empirica project-embed` — `docs-explain` now tells you when the collection is empty instead of silently using keyword mode |
| set `EMPIRICA_QDRANT_PATH` | remove it; file-based storage was removed in #45 and nothing reads the variable |
| run a persistent listener | restart the service after upgrading |
| upgrade from below 1.12 | read [`UPGRADE_TO_1.11.md`](UPGRADE_TO_1.11.md) for the intermediate content |

---

## Cross-references

- [`CHANGELOG.md`](../../CHANGELOG.md) — full 1.13.0 entry, including contributors
- [`docs/architecture/TRIGGER_MODEL.md`](../architecture/TRIGGER_MODEL.md) — wake-on-event vs interval vs cron
- [`docs/architecture/ARTIFACT_HYGIENE.md`](../architecture/ARTIFACT_HYGIENE.md) — the resolve/archive/delete discipline
- [`UPGRADE_TO_1.11.md`](UPGRADE_TO_1.11.md) · [`UPGRADE_TO_1.10.md`](UPGRADE_TO_1.10.md)
