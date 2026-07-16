# Supporting Components

**Smaller but essential components of the cognitive architecture.**

---

## Checkpoint Signer

**Module:** `empirica.core.checkpoint_signer`

### CheckpointSigner

Cryptographically signs epistemic checkpoints for audit integrity.

```python
signer = CheckpointSigner(
    private_key_path="~/.empirica/keys/private.pem",
    algorithm="Ed25519"
)

# Sign a checkpoint
checkpoint = {
    "session_id": "abc123",
    "phase": "CHECK",
    "vectors": {"know": 0.7, "uncertainty": 0.3},
    "timestamp": time.time()
}

signed_checkpoint = signer.sign(checkpoint)
# Adds: "signature": "base64-encoded-signature"

# Verify a signature
is_valid = signer.verify(signed_checkpoint)
```

**Use cases:**
- Compliance audit trails (HIPAA, SOX)
- Multi-agent trust verification
- Tamper detection for epistemic history

---

## Findings Deprecation Engine

**Module:** `empirica.core.findings_deprecation`

### FindingsDeprecationEngine

Automatically deprecates outdated findings based on evidence age and supersession.

```python
engine = FindingsDeprecationEngine(
    max_age_days=90,           # Deprecate findings older than 90 days
    supersession_threshold=0.7, # Deprecate if 70% superseded by newer findings
    check_frequency_hours=24
)

# Check findings for deprecation
deprecated = engine.check_and_deprecate(session_id="abc123")
for finding in deprecated:
    print(f"Deprecated: {finding['id']} - {finding['reason']}")

# Manual deprecation
engine.deprecate_finding(
    finding_id="xyz789",
    reason="Superseded by new architecture"
)
```

**Deprecation reasons:**
- `age_expired` - Finding exceeded max age
- `superseded` - Newer findings cover same topic
- `contradicted` - Evidence contradicts finding
- `manual` - Explicitly deprecated by user

---

## Signed Git Operations

**Module:** `empirica.core.git_ops`

### SignedGitOperations

Cryptographically signed git operations for audit integrity.

```python
ops = SignedGitOperations(
    repo_path=".",
    signing_key_path="~/.empirica/keys/private.pem"
)

# Create signed commit
ops.signed_commit(
    message="feat: Implement OAuth2 flow",
    author="empirica",
    sign=True
)

# Create signed tag
ops.signed_tag(
    tag_name="v1.3.0",
    message="Release with OAuth2",
    sign=True
)

# Verify commit signature
is_valid = ops.verify_commit("abc1234")
```

**Integration with Sentinel:**
- Sentinel can require signed commits for sensitive operations
- Audit trail tracks which AI made which changes
- Multi-agent scenarios use signatures for trust

---

## Validation Components

**Module:** `empirica.core.validation`

### CoherenceValidator

Validates epistemic coherence across vectors and findings.

```python
validator = CoherenceValidator()

result = validator.validate(
    vectors={"know": 0.9, "uncertainty": 0.8},  # Incoherent: high know + high uncertainty
    findings=["Verified auth implementation works"],
    unknowns=["How does auth work?"]  # Contradiction: finding says it works, unknown asks how
)

if not result.coherent:
    for issue in result.issues:
        print(f"Incoherence: {issue['type']} - {issue['description']}")
```

### EpistemicRehydration

Rehydrates epistemic state from storage after context compaction.

```python
rehydration = EpistemicRehydration(session_id="abc123")

state = rehydration.rehydrate(
    max_tokens=10000,
    include_findings=True,
    include_unknowns=True,
    include_lessons=True
)
# Returns compact representation of epistemic state
```

### HandoffValidator

Validates handoff reports for completeness and consistency.

```python
validator = HandoffValidator()

result = validator.validate(handoff_report)
if not result.valid:
    print(f"Invalid handoff: {result.errors}")
```

---

## Source Files

- `empirica/core/checkpoint_signer.py` - Cryptographic signing
- `empirica/core/findings_deprecation.py` - Finding lifecycle
- `empirica/core/git_ops/signed_operations.py` - Signed git ops
- `empirica/core/validation/` - Validation components
