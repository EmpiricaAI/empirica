"""
Checkpoint Signature Manager

Integrates cryptographic signing with git checkpoint system.
Signs git note SHAs to provide tamper-proof verification of epistemic state.

Architecture:
- Checkpoint created → Git note SHA generated
- SHA signed with AI identity (Ed25519)
- Signature stored in parallel git notes namespace
- Verification uses public key

Storage:
- Checkpoint: refs/notes/empirica/session/{session_id}/{phase}/{round}
- Signature: refs/notes/empirica/signatures/{session_id}/{phase}/{round}

Usage:
    from empirica.core.checkpoint_signer import CheckpointSigner

    signer = CheckpointSigner(ai_id="copilot")

    # Sign checkpoint
    signature_info = signer.sign_checkpoint(
        session_id="abc-123",
        phase="PREFLIGHT",
        round_num=1
    )

    # Verify checkpoint
    is_valid = signer.verify_checkpoint(
        session_id="abc-123",
        phase="PREFLIGHT",
        round_num=1,
        public_key_hex="..."
    )
"""

import json
import logging
import subprocess
from datetime import datetime, timezone  # type: ignore[reportAttributeAccessIssue]
from pathlib import Path
from typing import Any

from empirica.core.identity import AIIdentity

logger = logging.getLogger(__name__)


class CheckpointSigner:
    """
    Signs and verifies git checkpoint notes using Ed25519

    Provides cryptographic proof that checkpoints haven't been tampered with
    and were created by a specific AI identity.
    """

    def __init__(self, ai_id: str, git_repo_path: str | None = None, identity_dir: str | None = None):
        """
        Initialize checkpoint signer

        Args:
            ai_id: AI identifier (must have identity keypair)
            git_repo_path: Path to git repository (default: current dir)
            identity_dir: Custom identity directory
        """
        self.ai_id = ai_id
        self.git_repo_path = git_repo_path or Path.cwd()

        # Load AI identity
        self.identity = AIIdentity(ai_id=ai_id, identity_dir=identity_dir)
        try:
            self.identity.load_keypair()
            logger.info(f"Loaded identity for AI: {ai_id}")
        except FileNotFoundError:
            logger.warning(f"Identity not found for {ai_id}. Create with: empirica identity-create --ai-id {ai_id}")
            raise

    def sign_checkpoint(self, session_id: str, phase: str, round_num: int) -> dict[str, Any]:
        """
        Sign a git checkpoint note

        Process:
        1. Get checkpoint git note SHA
        2. Sign SHA with AI identity
        3. Store signature in parallel git notes namespace

        Args:
            session_id: Session UUID
            phase: Workflow phase (PREFLIGHT, CHECK, ACT, POSTFLIGHT)
            round_num: Round number

        Returns:
            Dict with signature info:
            {
                "ok": bool,
                "checkpoint_ref": str,
                "checkpoint_sha": str,
                "signature_ref": str,
                "signature_hex": str,
                "signed_at": str,
                "ai_id": str
            }
        """
        # Get checkpoint ref
        checkpoint_ref = f"empirica/session/{session_id}/{phase}/{round_num}"

        # Get checkpoint git note SHA
        try:
            result = subprocess.run(
                ["git", "rev-parse", f"refs/notes/{checkpoint_ref}"],
                capture_output=True,
                text=True,
                cwd=self.git_repo_path,
            )

            if result.returncode != 0:
                return {
                    "ok": False,
                    "error": "checkpoint_not_found",
                    "message": f"Checkpoint not found: {checkpoint_ref}",
                }

            checkpoint_sha = result.stdout.strip()

        except Exception as e:
            return {"ok": False, "error": "git_error", "message": str(e)}

        # Sign the SHA
        signature = self.identity.sign(checkpoint_sha.encode("utf-8"))
        signature_hex = signature.hex()

        # Create signature payload
        signature_payload = {
            "checkpoint_ref": checkpoint_ref,
            "checkpoint_sha": checkpoint_sha,
            "signature": signature_hex,
            "ai_id": self.ai_id,
            "public_key": self.identity.public_key_hex(),
            "signed_at": datetime.now(timezone.utc).isoformat(),
            "version": "1.0",
        }

        # Store signature in git notes
        signature_ref = f"empirica/signatures/{session_id}/{phase}/{round_num}"

        try:
            result = subprocess.run(
                ["git", "notes", "--ref", signature_ref, "add", "-f", "-F", "-", "HEAD"],
                input=json.dumps(signature_payload),
                capture_output=True,
                text=True,
                cwd=self.git_repo_path,
            )

            if result.returncode != 0:
                return {"ok": False, "error": "git_notes_error", "message": result.stderr}

            logger.info(f"✅ Signed checkpoint: {checkpoint_ref} ({checkpoint_sha[:8]})")

            return {
                "ok": True,
                "checkpoint_ref": checkpoint_ref,
                "checkpoint_sha": checkpoint_sha,
                "signature_ref": signature_ref,
                "signature_hex": signature_hex,
                "signed_at": signature_payload["signed_at"],
                "ai_id": self.ai_id,
                "message": "Checkpoint signed successfully",
            }

        except Exception as e:
            return {"ok": False, "error": "signing_error", "message": str(e)}

    def verify_checkpoint(
        self, session_id: str, phase: str, round_num: int, public_key_hex: str | None = None
    ) -> dict[str, Any]:
        """
        Verify a signed checkpoint

        Process:
        1. Load checkpoint git note SHA
        2. Load signature from git notes
        3. Verify signature with public key

        Args:
            session_id: Session UUID
            phase: Workflow phase
            round_num: Round number
            public_key_hex: Public key to verify with (default: use identity's own key)

        Returns:
            Dict with verification result:
            {
                "ok": bool,
                "valid": bool,
                "checkpoint_ref": str,
                "checkpoint_sha": str,
                "signed_by": str,
                "signed_at": str,
                "verified_with": str
            }
        """
        checkpoint_ref = f"empirica/session/{session_id}/{phase}/{round_num}"
        signature_ref = f"empirica/signatures/{session_id}/{phase}/{round_num}"

        # Get checkpoint SHA
        try:
            result = subprocess.run(
                ["git", "rev-parse", f"refs/notes/{checkpoint_ref}"],
                capture_output=True,
                text=True,
                cwd=self.git_repo_path,
            )

            if result.returncode != 0:
                return {"ok": False, "valid": False, "error": "checkpoint_not_found"}

            checkpoint_sha = result.stdout.strip()

        except Exception as e:
            return {"ok": False, "valid": False, "error": str(e)}

        # Get signature payload
        try:
            result = subprocess.run(
                ["git", "notes", "--ref", signature_ref, "show", "HEAD"],
                capture_output=True,
                text=True,
                cwd=self.git_repo_path,
            )

            if result.returncode != 0:
                return {
                    "ok": False,
                    "valid": False,
                    "error": "signature_not_found",
                    "message": f"No signature found for {checkpoint_ref}",
                }

            signature_payload = json.loads(result.stdout)

        except json.JSONDecodeError:
            return {"ok": False, "valid": False, "error": "invalid_signature_format"}
        except Exception as e:
            return {"ok": False, "valid": False, "error": str(e)}

        # Extract signature info
        signature_hex = signature_payload.get("signature")
        stored_checkpoint_sha = signature_payload.get("checkpoint_sha")
        signed_by = signature_payload.get("ai_id")
        signed_at = signature_payload.get("signed_at")
        public_key_from_payload = signature_payload.get("public_key")

        # Verify SHA matches
        if checkpoint_sha != stored_checkpoint_sha:
            return {
                "ok": True,
                "valid": False,
                "error": "sha_mismatch",
                "message": "Checkpoint SHA doesn't match signature",
                "checkpoint_sha": checkpoint_sha,
                "signed_sha": stored_checkpoint_sha,
            }

        # Use provided public key or extract from signature
        verify_public_key_hex = public_key_hex or public_key_from_payload

        if not verify_public_key_hex:
            return {
                "ok": False,
                "valid": False,
                "error": "no_public_key",
                "message": "No public key provided for verification",
            }

        # Verify signature
        try:
            signature_bytes = bytes.fromhex(signature_hex)
            public_key_bytes = bytes.fromhex(verify_public_key_hex)
            message = checkpoint_sha.encode("utf-8")

            is_valid = AIIdentity.verify(signature=signature_bytes, message=message, public_key_bytes=public_key_bytes)

            result = {
                "ok": True,
                "valid": is_valid,
                "checkpoint_ref": checkpoint_ref,
                "checkpoint_sha": checkpoint_sha,
                "signed_by": signed_by,
                "signed_at": signed_at,
                "verified_with": verify_public_key_hex[:16] + "...",
            }

            if is_valid:
                logger.info(f"✅ Valid signature for {checkpoint_ref} by {signed_by}")
            else:
                logger.warning(f"❌ Invalid signature for {checkpoint_ref}")

            return result

        except Exception as e:
            return {"ok": False, "valid": False, "error": "verification_error", "message": str(e)}

    def list_signed_checkpoints(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """
        List all signed checkpoints

        Args:
            session_id: Filter by session (optional)

        Returns:
            List of signed checkpoint info dicts
        """
        signatures = []

        # Build ref prefix
        if session_id:
            ref_prefix = f"refs/notes/empirica/signatures/{session_id}"
        else:
            ref_prefix = "refs/notes/empirica/signatures"

        # Get all signature refs
        try:
            result = subprocess.run(
                ["git", "for-each-ref", ref_prefix, "--format=%(refname)"],
                capture_output=True,
                text=True,
                cwd=self.git_repo_path,
            )

            if result.returncode != 0:
                return []

            refs = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]

            for ref in refs:
                # Parse ref: refs/notes/empirica/signatures/{session_id}/{phase}/{round}
                ref_parts = ref.split("/")
                if len(ref_parts) < 7:
                    continue

                sig_session_id = ref_parts[4]
                phase = ref_parts[5]
                round_num = ref_parts[6]

                # Get signature payload
                note_ref = ref[11:]  # Strip "refs/notes/"
                show_result = subprocess.run(
                    ["git", "notes", "--ref", note_ref, "show", "HEAD"],
                    capture_output=True,
                    text=True,
                    cwd=self.git_repo_path,
                )

                if show_result.returncode == 0:
                    try:
                        signature_payload = json.loads(show_result.stdout)
                        signatures.append(
                            {
                                "session_id": sig_session_id,
                                "phase": phase,
                                "round": int(round_num),
                                "checkpoint_sha": signature_payload.get("checkpoint_sha"),
                                "signed_by": signature_payload.get("ai_id"),
                                "signed_at": signature_payload.get("signed_at"),
                                "ref": note_ref,
                            }
                        )
                    except json.JSONDecodeError:
                        continue

            return sorted(signatures, key=lambda x: x.get("signed_at", ""), reverse=True)

        except Exception as e:
            logger.error(f"Failed to list signed checkpoints: {e}")
            return []
