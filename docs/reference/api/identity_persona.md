# Identity & Persona API

**Modules:** `empirica.core.identity`, `empirica.core.persona`
**Stability:** Beta

An **identity** is an Ed25519 keypair an AI signs with, so what it wrote can be attributed to it. A **persona** is a named
configuration for an AI working in one domain: its starting (prior) vectors, its thresholds and weights, what it may do,
and when the Sentinel should step in. Every persona is tied to a signing identity by its public key.

From the command line: `empirica identity-create --ai-id <name>`, `empirica identity-list`, `empirica identity-export
--ai-id <name>`. Keys live under `.empirica/identity/` in the practice.

---

## AIIdentity

```python
from empirica.core.identity.ai_identity import AIIdentity

identity = AIIdentity(ai_id="claude-code", identity_dir="/tmp/empirica-doc/identity")
identity.generate_keypair()
identity.save_keypair(overwrite=True)  # <identity_dir>/claude-code.key and .pub

message = b"checkpoint 42"
signature = identity.sign(message)  # bytes in, bytes out

public_key = bytes.fromhex(identity.public_key_hex())
assert AIIdentity.verify(signature, message, public_key)  # static: (signature, message, public key bytes)
assert not AIIdentity.verify(signature, b"tampered", public_key)

exported = identity.export_public_key()  # {"ai_id", "created_at", "public_key", "metadata"}
```

`sign` raises `RuntimeError` until a key is generated or loaded. To use a saved key: `AIIdentity(ai_id).load_keypair()`.
With `EMPIRICA_IDENTITY_PASSWORD` set, `save_keypair` encrypts the private key and `load_keypair` needs the same password.

`IdentityManager(identity_dir=None)` wraps the directory: `create_identity(ai_id, overwrite=False)`,
`load_identity(ai_id)`, `identity_exists(ai_id)` and `list_identities()`.

### Signed assessments (EEP-1)

`empirica.core.identity.signature` signs an output together with the vectors it was produced under, so a reader can check
both who made it and that neither was changed afterwards.

```python
from empirica.core.identity.ai_identity import AIIdentity
from empirica.core.identity.signature import sign_assessment, verify_eep1_payload, verify_signature

identity = AIIdentity(ai_id="claude-code", identity_dir="/tmp/empirica-doc/identity")
identity.generate_keypair()

package = sign_assessment("the final answer", {"know": 0.9, "uncertainty": 0.1}, identity, model_id="claude-opus")
assert verify_signature(package)  # the public key travels in the package as creator_id

report = verify_eep1_payload(package, content="the final answer")  # signature, content hash, timestamp
print(report)
```

`create_eep1_payload(content, epistemic_state, ai_id, ...)` builds the unsigned payload, and
`compute_cascade_trace_hash(git_log)` gives the hash to pass as `cascade_trace_hash`.

---

## PersonaProfile

A persona is one `PersonaProfile`. The parts:

| Part | Holds |
|---|---|
| `SigningIdentityConfig` | `user_id`, `identity_name`, `public_key` (64 hex characters), `reputation_score` (default 0.5) |
| `EpistemicConfig` | `priors` (all 13 vectors, each 0 to 1), `thresholds`, `weights` (must sum to 1), `focus_domains` |
| `CapabilitiesConfig` | `can_spawn_subpersonas`, `can_call_external_tools`, `can_modify_code`, `can_read_files`, `requires_human_approval`, `max_investigation_depth`, `restricted_operations` |
| `SentinelConfig` | `reporting_frequency`, `escalation_triggers` (a list of `EscalationTrigger`), `timeout_minutes`, `max_cost_usd`, `requires_sentinel_approval_before_act` |
| `PersonaMetadata` | `created_by`, `created_at`, `modified_at`, `description`, `tags`, `parent_persona`, `derived_from`, `verified_sessions` |

```python
from empirica.core.identity.ai_identity import AIIdentity
from empirica.core.persona.persona_profile import (
    CapabilitiesConfig,
    EpistemicConfig,
    EscalationTrigger,
    PersonaProfile,
    SentinelConfig,
    SigningIdentityConfig,
)

key = AIIdentity(ai_id="security-lead", identity_dir="/tmp/empirica-doc/identity")
key.generate_keypair()

VECTORS = ["engagement", "know", "do", "context", "clarity", "coherence", "signal", "density",
           "state", "change", "completion", "impact", "uncertainty"]
priors = dict.fromkeys(VECTORS, 0.6) | {"completion": 0.05, "uncertainty": 0.2}

profile = PersonaProfile(
    persona_id="security-lead",          # lowercase letters, digits, _ and -
    name="Security Lead",
    version="1.0.0",                     # semantic version
    signing_identity=SigningIdentityConfig(
        user_id="david", identity_name="security-lead", public_key=key.public_key_hex()
    ),
    epistemic_config=EpistemicConfig(
        priors=priors,
        thresholds={"uncertainty_trigger": 0.3, "confidence_to_proceed": 0.85},
        focus_domains=["security", "authentication"],
    ),
    capabilities=CapabilitiesConfig(restricted_operations=["push_to_main"]),
    sentinel_config=SentinelConfig(
        escalation_triggers=[EscalationTrigger(condition="uncertainty > 0.8", action="escalate", priority="high")]
    ),
)

assert profile.get_type() == "security"  # from the focus domains
assert PersonaProfile.from_dict(profile.to_dict()).persona_id == "security-lead"
```

Construction validates: a missing or out-of-range prior, weights that do not sum to 1, a malformed `persona_id` or
version, or a public key that is not 64 hex characters raise `ValueError`.

`PersonaManager(personas_dir=None)` stores profiles as files (default `.empirica/personas/`):
`create_persona(persona_id, name, ...)`, `save_persona(profile, overwrite=False)`, `load_persona(persona_id)`,
`list_personas()`, `delete_persona(persona_id)`.

---

## Implementation files

- `empirica/core/identity/ai_identity.py`: `AIIdentity`, `IdentityManager`
- `empirica/core/persona/persona_profile.py`: `PersonaProfile` and its parts
- `empirica/core/persona/persona_manager.py`: `PersonaManager`
