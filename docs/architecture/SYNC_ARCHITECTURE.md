# Empirica Sync Architecture

How epistemic data moves between devices and clones. **SQLite is the working store. Git notes are the replicated mirror.** Qdrant is derived.

---

## The model

| Layer | Role | Where | Replicated |
|-------|------|-------|------------|
| SQLite (`.empirica/sessions/sessions.db`) | Working store: every verb reads and writes it | Local, gitignored | No |
| Git notes (`refs/notes/...`) | Replicated mirror of the artifacts | In the repo's object store | Yes, by `sync-push` / `sync-pull` |
| Qdrant | Semantic search over artifacts | Derived | Rebuilt from SQLite, not synced |

A clone of the repo has no `.empirica/`, so on its own it has none of the epistemic state. The notes carry it, and `rebuild` restores SQLite from them.

**What the notes do not carry.** Calibration rows (`grounded_beliefs`, `grounded_verifications`, `calibration_trajectory`) live only in SQLite and have no note to restore from. `.breadcrumbs.yaml` is a generated export of calibration for session-start injection; this repo gitignores it. Identity keys and credentials are device-local.

### Write path

A `*-log` verb (`finding-log`, `unknown-log`, `deadend-log`, `mistake-log`, `assumption-log`, `decision-log`, `source-add`) writes the SQLite row and the matching git note. Goals, tasks, handoffs, session-phase vectors, checkpoints and mailbox messages have notes of their own. Embedding into Qdrant happens when it is available. The note is the copy that travels.

### Note layout

Notes live under refs of their own. Most types get one ref per artifact; a few (`checkpoints`, `receipts`) are a single ref whose notes hang off commits:

```
refs/notes/empirica/<type>/<id>                        findings, unknowns, dead_ends, mistakes,
                                                       assumptions, decisions, goals, tasks, sources, ...
refs/notes/empirica/cascades/<session>/<id>
refs/notes/empirica/session/<session>/<PHASE>/<round>  PREFLIGHT / CHECK / POSTFLIGHT vectors
refs/notes/breadcrumbs
refs/notes/empirica-precompact
refs/notes/empirica-archive/<type>/<id>                local only, see below
```

The live set is whatever the repo holds; list it rather than trusting a doc:

```bash
git for-each-ref refs/notes --format='%(refname)'
```

**Archive refs are never pushed.** When `delete-artifacts` or note reconciliation removes an artifact from the active graph, the note moves from `refs/notes/empirica/<type>/<id>` to `refs/notes/empirica-archive/<type>/<id>`, so the journey is kept locally. Resolutions of kept artifacts are stamped into their notes. The push refspecs cover `refs/notes/empirica/*`, `refs/notes/breadcrumbs` and `refs/notes/empirica-precompact`; the archive namespace matches none of them.

---

## Verbs

| Verb | Does |
|------|------|
| `sync-config [key] [value]` | Show or set sync settings. Keys: `enabled`, `remote`, `notes_remote`, `code_remote`, `auto_push_on`, `visibility`, `provider` |
| `sync-push [--remote R] [--dry-run] [--force]` | Push the note namespaces above to the notes remote |
| `sync-pull [--remote R] [--rebuild] [--force]` | Fetch the same namespaces; `--rebuild` then restores SQLite from the notes |
| `sync-status [--remote R] [--local]` | Local note counts, the destinations, and whether the notes actually replicate. `--local` skips the network call |
| `rebuild [--from-notes] [--qdrant] [--qdrant-only] [--reflexes-only [--apply]]` | Restore SQLite from notes, or re-embed Qdrant, or restore reflex rows |
| `doctor --reconcile-notes [--apply]` | Repair notes/SQLite divergence: archive notes whose artifact SQLite deleted, stamp resolutions SQLite recorded. Plan only without `--apply` |

There is no workspace-level sync verb. `workspace-overview` lists projects, and each project syncs with its own `sync-push` / `sync-pull`.

---

## Where notes go

**No remote has a default.** `remote`, `notes_remote` and `code_remote` are unset until a person sets them, and every verb refuses rather than guesses. A wrong guess about a remote is a publication decision made by a default: with `origin` pointing at a public GitHub repo one default published, and with no `origin` another synced nowhere, and neither said which case it was.

- **Notes destination** resolves as: `--remote` flag, then `notes_remote`, then `remote`. If none is set, `sync-push` and `sync-pull` refuse, name the git remotes the repo does have, and print the command to choose one.
- **Code destination** is `code_remote`, used only for the opt-in auto-push below. Unset means refuse.
- **Public hosts are refused for notes.** `sync-push` refuses a remote whose URL is GitHub, GitLab or Bitbucket unless `--force` is given, because notes carry findings, mistakes and mesh messages. `sync-pull` does not apply this check.
- **`auto_push_on`** accepts only `postflight`. When set, a POSTFLIGHT pushes code to `code_remote`, refuses on a dirty tree, and reports whether the remote ref equals local HEAD afterwards. `session_end` is rejected by name because a trigger that fires on a crash fires when state is least trustworthy.

```bash
empirica sync-config                         # show config, destinations, available git remotes
empirica sync-config notes_remote <remote>   # where notes go
empirica sync-config code_remote <remote>    # where code goes, if you opt in
empirica sync-config auto_push_on postflight # opt in; empty is off
```

Settings live under `sync:` in `.empirica/config.yaml`.

### Private notes for a public repo

If the code repo is public, push notes to a different, private remote:

```bash
git remote add notes-private git@host:you/project-notes-private.git
empirica sync-config notes_remote notes-private
empirica sync-push            # notes go to notes-private; code still goes where you push it
```

On a second machine: clone the code repo, add the same remote, set `notes_remote`, then `empirica sync-pull --rebuild`, and check with `empirica sync-status`.

---

## What a push and a pull do

`sync-push` runs `git push <remote>` once per namespace: `refs/notes/empirica/*`, `refs/notes/breadcrumbs`, `refs/notes/empirica-precompact`. `sync-pull` fetches the same three. They are ordinary git refspecs, so conflicts and non-fast-forward rejections are git's.

**Exit code is not replication.** A refspec that matches nothing exits 0 and pushes nothing. After a push, the verb counts the note refs on the remote and reports `replicated`, `partial` or `not_replicating` from whether the remote moved. `sync-status` makes the same comparison on request: local note refs against `git ls-remote <remote> 'refs/notes/*'`, reported as `replicated`, `behind`, `not_replicating`, `nothing_to_replicate` or `unknown` (remote unreachable). A configured remote does not mean the notes are there, and the status says which it is.

Both counts leave out `refs/notes/empirica-archive/*` (the local count and the `ls-remote` count), because no push carries it. Before 1.14.9 the local count included the archive refs, so a repo with archived notes read `behind` or `partial` permanently even when everything pushable had been pushed.

---

## Rebuild

`rebuild` (the same as `rebuild --from-notes`, and what `sync-pull --rebuild` runs) restores from the notes:

- findings, unknowns (including resolved), dead-ends and mistakes;
- goals;
- stub `projects` and `sessions` rows the restored artifacts need as foreign keys, and stub goals for goal ids the artifacts reference but no note holds.

It does not read the decisions, assumptions, tasks, sources, handoff or message notes. Those are written to notes and replicated, but this rebuild does not restore them into SQLite. Calibration tables and reflex rows are not restored either, except by the next mode.

- **`rebuild --qdrant`**: rebuild SQLite from notes first, then re-embed Qdrant.
- **`rebuild --qdrant-only`**: re-embed Qdrant from the current SQLite and skip the notes import. This is the safe resync after direct SQL or bulk changes not yet persisted to notes; the default path would revert them, because notes win.
- **`rebuild --reflexes-only [--apply]`**: restore only the reflex rows (PREFLIGHT, CHECK, POSTFLIGHT vectors) from the session-phase notes, and nothing else. It previews unless `--apply`. Identity is `(session_id, phase, round)`, encoded in the ref name `refs/notes/empirica/session/<session_id>/<PHASE>/<round>`, so an existing row is never replaced and the mode is idempotent. It keeps each note's own timestamp, transaction id and reasoning, and only the vectors the note carries: the rest stay NULL, not 0.5. It skips and counts: sessions with no `sessions` row (named in the output), notes with no vectors, notes that disagree with their ref name, and the all-0.5 phantom CHECK rows that `delete-artifacts` purged on purpose. A ref can hold several notes because the writer reuses `<PHASE>/<round>` across transactions: notes under a ref that is not present are restored, but when the identity is already present the other notes under it are not looked at (`multi_note_refs` counts them).

Because notes are the source for a rebuild, a note that disagrees with SQLite is a pending revert, not a stale copy. That is why gardening writes through to the notes (archive on delete, stamp on resolve) and why `doctor --reconcile-notes` exists for divergence that predates it.

---

## Not built

- **Workspace orchestration.** A single verb that syncs several projects in dependency order does not exist. Nothing reads a `.empirica-workspace` sync policy.
- **Note signing.** Notes are not cryptographically signed; trust is whatever git transport and remote access control give.
- **Qdrant replication.** Embeddings are rebuilt from SQLite, never synced.
- **Merge policy for concurrent writers.** Pushes and fetches are plain git refspecs; no custom note-merge strategy is applied.

---

## Source files

- `empirica/cli/command_handlers/sync_commands.py` - `sync-*` and `rebuild`, refspecs, replication verdict
- `empirica/core/sync_remotes.py` - remote resolution and the refusal payload
- `empirica/core/auto_push.py` - opt-in code push on POSTFLIGHT
- `empirica/core/canonical/empirica_git/` - one store per artifact type (`finding_store.py`, `unknown_store.py`, `goal_store.py`, ...), `note_lifecycle.py` (archive and stamp), `note_reconcile.py`
- `empirica/core/canonical/reflex_import.py` - `rebuild --reflexes-only`
