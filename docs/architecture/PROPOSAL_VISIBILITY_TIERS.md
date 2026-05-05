# PROPOSAL: Three-Tier Visibility for Empirica Artifacts

**Status:** Draft (2026-04-30)
**Author:** David S. L. Van Assche + Claude Code (Opus 4.7 1M)
**Related:**
- [`PROPOSAL_AI_SERVICE_SCANNER.md`](PROPOSAL_AI_SERVICE_SCANNER.md) — depends on this for finding visibility classification
- [`../human/developers/Security/PRIVACY_AGENT.md`](../human/developers/Security/PRIVACY_AGENT.md) — existing privacy doc; this extends it
- Prior decision: *consent-based sharing with type defaults* (recovered from project memory) — this spec is the implementation

---

## Problem statement

Empirica today has only two visibility states:

- **Public** — tracked + pushed; world can read
- **Local** — `.empirica/` is gitignored; single-machine only

This is a false dichotomy. Real epistemic work has three states:

1. **Public** — code, README, public docs, pushed canonical content
2. **Private-shared** — findings, decisions, voice profiles, theory drafts, paper outlines, sensitive corpus excerpts. *Team can read, world cannot.* Currently **impossible** without leaking.
3. **Local-only** — session caches, machine-specific runtime state, scratch

The missing middle has three concrete consequences:

1. **Coverage measurement is incoherent across machines.** Each developer has a different artifact universe (gitignored ≠ shared denominator). Coverage as a team metric requires a deterministic universe — `git-crypt` provides it.
2. **Team collaboration is broken.** David + Philipp share an empirica project but cannot compound their findings without leaking to GitHub. The current options are "leak everything" or "share nothing."
3. **The "your data stays yours" promise is hollow at team scale.** Currently truthful because gitignored = local. After this spec: truthful because shared with team, encrypted in transit and at rest, never reaches the world.

---

## The visibility primitive

Every empirica artifact carries a `visibility` field at write time:

```python
visibility: Literal['public', 'shared', 'local'] = 'shared'  # default
```

**Default is `shared`** — private to the team but co-versioned. This is the safest default: artifacts compound across collaborators by default, but never leak to the world.

The agent that writes the artifact (POSTFLIGHT pipeline, finding-log, decision-log, etc.) classifies visibility at emission time. Classification is itself a measurable AI judgment — same shape as the security-canon citation specced for the scanner.

Classification heuristics (agent-applied):
- **Always private (`local`):** raw secrets, session tokens, OAuth credentials, keychain values, environment variable VALUES (never their names)
- **Always shared (`shared`):** findings, decisions, dead-ends, mistakes, assumptions, unknowns, voice profiles, theory drafts, scanner findings, paper drafts, customer names + PII
- **Public-eligible (`public`):** generic technical patterns, public-RFC citations, MIT-licensed example code, documentation about empirica itself

The agent emits with `visibility=shared` by default. Promotion to `public` requires explicit reasoning ("this artifact contains no PII, is generic, and references only public sources"). Demotion to `local` is a security action.

---

## git-crypt as the implementation

[git-crypt](https://github.com/AGWA/git-crypt) is mature, maintained, simple, and exactly fits the use case. It transparently encrypts files matching `.gitattributes` patterns at push, decrypts at pull (for collaborators with the key).

**Setup:**

```bash
git-crypt init                    # one-time, generates symmetric key
git-crypt add-gpg-user <gpg-id>   # for each team member
```

**`.gitattributes` declares encrypted paths:**

```
.empirica/findings/**            filter=git-crypt diff=git-crypt
.empirica/decisions/**           filter=git-crypt diff=git-crypt
.empirica/sessions/**            filter=git-crypt diff=git-crypt
.empirica/voice/**               filter=git-crypt diff=git-crypt
.empirica/theory/**              filter=git-crypt diff=git-crypt
.empirica/grounded_*             filter=git-crypt diff=git-crypt
docs/research/**                 filter=git-crypt diff=git-crypt
```

**`.gitignore` retains `local` tier:**

```
.empirica/sessions/sessions.db   # truly local — large + machine-specific
.empirica/cache/                 # truly local — performance cache
.empirica/instance_*             # truly local — instance state
```

Encrypted files in the public repo appear as `[git-crypted]` to non-key-holders. With key, indistinguishable from normal files.

---

## Three-tier mapping

| Tier | Storage | Pushed? | Encrypted? | Example artifacts |
|---|---|---|---|---|
| **Public** | tracked, plaintext | Yes | No | source code, README, public docs, pyproject.toml |
| **Shared** | tracked, git-crypt | Yes | Yes | findings, decisions, voice profiles, paper drafts, scanner findings |
| **Local** | gitignored | No | N/A | sessions.db, instance_*, cache/, machine-specific runtime state |

The agent's visibility classification maps directly onto these tiers.

---

## CLI surface

```bash
empirica visibility list                         # show artifacts by tier
empirica visibility show <artifact-id>           # show one artifact's tier + reasoning
empirica visibility reclassify <id> --to public  # promote (requires confirmation)
empirica visibility reclassify <id> --to local   # demote (security action)
empirica visibility audit                        # AI re-runs classification on all artifacts; flags drift
empirica visibility setup                        # one-time git-crypt setup wizard
```

---

## Cockpit integration

New panel below `#compliance` (mirrors the pattern from 1.9.0):

```
🔒 visibility — 1247 shared · 42 public · 89 local (clean)
                ⚠ 3 artifacts flagged for reclassification (12h ago)
```

`aggregate_instance_state` reads `~/.empirica/visibility_audit_<project_id>.json` (written by the audit loop) and surfaces the breakdown per instance. Click to expand shows reclassification candidates.

---

## Caveats + risks

1. **git-crypt does not encrypt filenames or paths.** A file named `.empirica/decisions/2026-04-30-credential-leak-incident.md` leaks the filename even with encrypted content. **Mitigation:** sensitive artifacts use opaque UUID filenames; human-readable names are encoded in the encrypted body.
2. **Lost key = lost history.** No recovery. Mitigation: GPG-based multi-user setup so any team member's key can decrypt; key escrow for solo users (encrypted backup off-machine).
3. **Performance.** git-crypt operates per-file; large repos with many encrypted files have slower clones. Acceptable for empirica's current scale.
4. **Branch-level visibility doesn't compose with file-level visibility.** Once a file is in a shared tier, all branches see it as shared. No per-branch tier overrides. Acceptable simplification.
5. **The agent's classification might be wrong.** False-public is a security incident; false-private is a productivity drag. **Default to shared,** require explicit reasoning for public promotion.
6. **Key sharing is a real workflow.** Solo users: key on-disk in `~/.git-crypt/key` (off-repo). Team: GPG public keys collected in `.git-crypt/keys/` directory tracked in repo. New collaborator workflow needs documentation.

---

## Phasing

| Phase | Version | Scope | Effort |
|---|---|---|---|
| **Phase 0** | 1.9.0 | `visibility` field on all artifacts (metadata only, no encryption yet). Agents classify at write time. CLI list/show. | ~1 day |
| **Phase 1** | 1.9.0 | git-crypt integration: `.gitattributes` declarations, `empirica visibility setup` wizard, key management docs. Encrypts existing shared-tier artifacts. | ~2 days |
| **Phase 2** | 1.9.0 | `empirica visibility audit` loop (biweekly): re-classify existing artifacts, flag drift, propose reclassifications. Cockpit panel. | ~3 days |
| **Phase 3** | 1.9.x | Per-team key-sharing workflows; GPG-based multi-user setup; encrypted backup tooling. Compliance reporting (data-residency stories). | ~1 week |

Phase 0 alone is useful — agents start emitting with explicit visibility, even before encryption. Phase 1 closes the leak. Phase 2 makes it self-maintaining. Phase 3 productionizes for teams.

---

## Why this matters strategically

1. **Brand truth.** "Your epistemic data is yours" becomes literally true at team scale, not just solo.
2. **Coverage measurement gets stronger.** Coverage paper's empirical section assumes a stable artifact universe. Without this primitive, coverage is per-machine; with it, coverage is per-team.
3. **Privacy classification is a first-class AI primitive.** Same shape as security-citation classification (scanner) and grounded calibration (existing). All three are "the AI applies a measurable judgment over a defined universe with citation/coverage." Compounding architecture.
4. **Compliance posture.** EU AI Act Art. 10 (data governance), GDPR Art. 32 (security of processing). Encrypted-at-rest team-shared artifacts is what auditors want to see.
5. **Dogfood-driven.** The paper outline you flagged needs this. The scanner findings need this. The voice profiles need this. Every empirica user runs into this gap eventually.

---

## Open questions

1. **Default tier for new artifacts.** Spec says `shared`. Alternative: `local` with explicit promotion. **Vote: shared** — privacy-by-default but team-collaboration-by-default, which is the more useful invariant.
2. **Solo-user friction.** Setting up git-crypt for one person is overhead they may not want. Should `empirica visibility setup --solo` skip the encryption layer entirely (everything stays gitignored as today)? **Vote: yes** — one-flag opt-out for solo developers; team users get the full setup.
3. **Migration of existing artifacts.** Current empirica installations have artifacts in `.empirica/` (gitignored). Phase 1 needs a migration: `empirica visibility migrate` that scans existing artifacts, applies classification, re-files into the new tier structure. Should this prompt for each artifact or batch-classify? **Vote: batch-classify with a dry-run report; user reviews before commit.**

---

## Acceptance criteria for Phase 0

- [ ] `visibility` field added to all artifact emission paths (finding-log, decision-log, dead-end-log, mistake-log, assumption-log, log-artifacts batch)
- [ ] Agents classify at write time (default `shared`; promotion requires explicit reasoning)
- [ ] `empirica visibility list` and `show` commands work
- [ ] Tests cover: classification accuracy on a fixed sample, default behavior, override semantics
- [ ] No encryption yet — Phase 0 is metadata-only

---

## What this enables next

Once visibility tiers exist:
- Scanner findings can be classified appropriately (most are shared, some public)
- Voice profiles can be team-shared (Philipp's voice + David's voice in same project)
- Theory drafts get version control without leaking
- Paper outlines version controllably
- Cross-team empirica deployments become viable
- Compliance reporting gains the data-residency dimension

This unblocks the team-collaboration story that empirica has been deferring since 1.0.
