# `module.yaml` — the practice-module manifest

**Core owns this schema.** `empirica/core/modules/manifest.py` is the source of
truth and `extra="forbid"` at every level, so a key documented anywhere else and
absent here is not a variant spelling — it is a manifest that fails validation.

This page exists because that was not true for a while. The only reference
describing the mechanism lived in a peer repo, documented a key core rejects, and
two practices followed it in good faith — one shipping an invalid manifest for
several commits. A reference for a schema belongs with the schema.

Validate before believing anything below:

```bash
empirica module validate path/to/module.yaml            # exit 1 on invalid
empirica module validate path/to/module.yaml --output json | jq .declarations
```

---

## Shape

```yaml
empirica_module:
  name: empirica-workspace        # → ~/.claude/plugins/local/<name>/
  seat_name: empirica-workspace   # canonical practice id (required even with no seat block)
  version: "0.3.0"
  visibility: private             # public | private | enterprise

  requires:
    empirica_core: ">=1.13.35"
    cortex_api: ">=v1"
    prompts: [...]                # layers this practice CONSUMES
    skills:  [...]
    declined:                     # layers it deliberately does NOT — see below
      prompts: {name: reason}
      skills:  {name: reason}

  seat:                           # OPTIONAL — practices with a manifest and
    import: docs/seat.md          # practices with a seat are different sets
    mode: inject                  # inject | dedicated

  artifacts:      {...}           # → empirica module fetch
  provides:       {...}           # mcp / skills / apis / domains this practice offers
  requires_runtime:               # presence-validated at install
    env:    [...]                 # names only; values are never read into the provisioner
    topics: [...]
    mcp:    [...]
    secrets_ref: doppler://...    # a manager REFERENCE, never a raw key (validated)
```

---

## Declarations are three-state, and the third carries a reason

`requires.prompts` and `requires.skills` say what a practice **consumes**.
`requires.declined` says what it **deliberately does not, and why**.

```yaml
requires:
  prompts:
    - empirica-system-prompt.md
    - empirica-org-prompt.md
  declined:
    prompts:
      empirica-crm-prompt.md: "capability-scoped to seats running the CRM CRUD verbs"
```

**Why the third state exists.** A gate that reports *"this seat has the capability
and no declaration"* has only two states to read: declared, and nothing. A
considered refusal and a practice that simply forgot both land in *nothing* — they
are the same bytes to a parser. So the gate fires on the most carefully-declared
seat in the fleet, and **a check that cries wolf gets silenced the first time it
does.**

Every manifest in the fleet already carried its refusals, as YAML comments. A
comment is a note to the next human and invisible to every reader.

**Why the reason is required.** A bare list of declined names moves the silence one
level up: you would know a layer was refused and not why, so a gate could report the
fact and nothing anyone could act on. An empty or whitespace reason is a validation
error naming the entry.

**Two rules the validator enforces:**

- a declined entry needs a non-empty reason;
- a layer may not be both consumed and declined — a contradiction that validates is
  worse than one that does not, because a gate would answer whichever branch it
  checked first.

### `provides` has the same three states, and its wrong answer costs more

```yaml
provides:
  domains: [remote-ops, provisioning, fleet-sync]
  declined:
    domains:
      governance: "not a governance practice — escalations here sit looking coordinated"
      commercial: "no commercial surface"
```

`requires.declined` answers *what does this practice not consume* — wrong, and you get
a missing file, which fails at first use. `provides.declined` answers *what is this
practice not FOR* — wrong, and you get a **misroute**, which fails silently for as long
as the sender is patient, because the work lands somewhere that looks like it is
handling it.

A standards-submission deadline was once escalated to a practice whose whole scope is
remote-ops: correctly addressed, entirely misrouted, and it sat for a day looking
coordinated. The exclusion existed the whole time — as a YAML comment.

Same rules as the requires block: the reason is required, and an axis may not be both
provided and declined.

**The positive state is named for its block.** A domain a practice offers reads back as
`provides`, not `consumes` — `skills` appears on both blocks meaning different things,
so pass `side="requires"` or `side="provides"` to disambiguate it.

### Read declarations through the reader, not the lists

```python
from empirica.core.modules.manifest import declaration_state, load_manifest

state, reason = declaration_state(load_manifest(path), "empirica-crm-prompt.md")
# ("consumes", None) | ("declined", "<reason>") | ("undeclared", None)
#   kind="skills" for the skills axis
```

Every gate in the fleet asking the same question the same way is the point.
Reading the lists directly is how the two axes drift apart — and `declaration_state`
raises on an unknown `kind` rather than resolving to `undeclared`, because a typo
that silently reports the whole fleet as undeclared is a gate being believed while
answering a question nobody asked.

**A field with no reader is what let this drift persist.** Nothing compared declared
intent to reality, so nobody could see that the intent had nowhere to live. Any
future declaration field should ship with its reader in the same change.

---

## Compatibility

`declined` defaults to empty, so every manifest that validated before still
validates. The four practice manifests in the fleet were checked against this schema
before it shipped.

`extra="forbid"` is deliberate and stays: a mis-spelled key is a loud error rather
than a silently-ignored field. In particular a `consumes:` block — top-level or
nested under `prompts` — is **not** valid and never was.
