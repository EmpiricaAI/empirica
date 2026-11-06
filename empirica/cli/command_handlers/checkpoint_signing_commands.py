"""
Checkpoint Signing CLI Commands

Commands for cryptographically signing and verifying git checkpoints.

Commands:
- checkpoint-sign: Sign a checkpoint with AI identity
- checkpoint-verify: Verify a signed checkpoint
- checkpoint-signatures: List all signed checkpoints

Usage:
    empirica checkpoint-sign --session-id abc-123 --phase PREFLIGHT --round 1 --ai-id copilot
    empirica checkpoint-verify --session-id abc-123 --phase PREFLIGHT --round 1
    empirica checkpoint-signatures --session-id abc-123
"""

import json
import logging
import sys

from ..cli_utils import handle_cli_error, printed

logger = logging.getLogger(__name__)


def handle_checkpoint_sign_command(args):
    """Sign a checkpoint with AI identity"""
    try:
        from empirica.core.checkpoint_signer import CheckpointSigner

        session_id = args.session_id
        phase = args.phase
        round_num = args.round
        ai_id = args.ai_id
        output_format = getattr(args, "output", "default")

        # Initialize signer
        try:
            signer = CheckpointSigner(ai_id=ai_id)
        except FileNotFoundError:
            error_result = {
                "ok": False,
                "error": "identity_not_found",
                "message": f"Identity not found for AI: {ai_id}",
                "hint": f"Create identity: empirica identity-create --ai-id {ai_id}",
            }

            if output_format == "json":
                print(json.dumps(error_result, indent=2))
            else:
                print(f"❌ Identity not found for AI: {ai_id}")
                print("\n💡 Create identity first:")
                print(f"   empirica identity-create --ai-id {ai_id}")

            sys.exit(1)

        # Sign checkpoint
        result = signer.sign_checkpoint(session_id=session_id, phase=phase, round_num=round_num)

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            if result["ok"]:
                print("✅ Checkpoint signed successfully")
                print("\n📋 Details:")
                print(f"   Checkpoint: {result['checkpoint_ref']}")
                print(f"   SHA: {result['checkpoint_sha'][:16]}...")
                print(f"   Signature: {result['signature_hex'][:32]}...")
                print(f"   Signed by: {result['ai_id']}")
                print(f"   Signed at: {result['signed_at']}")
                print("\n💾 Signature stored in:")
                print(f"   refs/notes/{result['signature_ref']}")
                print("\n🔍 Verify with:")
                print(f"   empirica checkpoint-verify --session-id {session_id} --phase {phase} --round {round_num}")
            else:
                print(f"❌ Failed to sign checkpoint: {result.get('message')}")
                sys.exit(1)

        return printed(result, args)

    except Exception as e:
        handle_cli_error(e, "checkpoint signing")
        sys.exit(1)


def handle_checkpoint_verify_command(args):
    """Verify a signed checkpoint"""
    try:
        from pathlib import Path

        from empirica.core.checkpoint_signer import CheckpointSigner
        from empirica.core.identity import AIIdentity

        session_id = args.session_id
        phase = args.phase
        round_num = args.round
        ai_id = getattr(args, "ai_id", None)
        public_key_hex = getattr(args, "public_key", None)
        output_format = getattr(args, "output", "default")

        # If AI ID provided, load their identity to get public key
        if ai_id and not public_key_hex:
            try:
                identity = AIIdentity(ai_id=ai_id)
                identity.load_keypair()
                public_key_hex = identity.public_key_hex()
            except FileNotFoundError:
                if output_format != "json":
                    print(f"⚠️  Identity not found for {ai_id}, will use embedded public key from signature")

        # For verification, we don't actually need to load a signer identity
        # We just use the public key from the signature payload
        # Create a minimal signer instance (won't be used for actual signing)

        class VerificationSigner(CheckpointSigner):
            """Minimal signer for verification only - doesn't need identity"""

            def __init__(self, git_repo_path=None):
                """Initialize verification-only signer without identity loading."""
                self.git_repo_path = git_repo_path or Path.cwd()
                self.ai_id = "verifier"
                # Skip identity loading for verification

        signer = VerificationSigner()

        # Verify checkpoint
        result = signer.verify_checkpoint(
            session_id=session_id, phase=phase, round_num=round_num, public_key_hex=public_key_hex
        )

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            if not result["ok"]:
                print(f"❌ Verification failed: {result.get('message', result.get('error'))}")
                sys.exit(1)

            if result["valid"]:
                print("✅ Valid signature")
                print("\n📋 Details:")
                print(f"   Checkpoint: {result['checkpoint_ref']}")
                print(f"   SHA: {result['checkpoint_sha'][:16]}...")
                print(f"   Signed by: {result['signed_by']}")
                print(f"   Signed at: {result['signed_at']}")
                print(f"   Verified with: {result['verified_with']}")
                print("\n🔒 Checkpoint integrity confirmed")
            else:
                print("❌ Invalid signature")
                print("\n⚠️  Checkpoint may have been tampered with")
                print(f"   Checkpoint: {result.get('checkpoint_ref')}")
                if result.get("error") == "sha_mismatch":
                    print(f"   Expected SHA: {result.get('signed_sha', '')[:16]}...")
                    print(f"   Actual SHA: {result.get('checkpoint_sha', '')[:16]}...")
                sys.exit(1)

        return printed(result, args)

    except Exception as e:
        handle_cli_error(e, "checkpoint verification")
        sys.exit(1)


def handle_checkpoint_signatures_command(args):
    """List all signed checkpoints"""
    try:
        from empirica.core.checkpoint_signer import CheckpointSigner

        session_id = getattr(args, "session_id", None)
        output_format = getattr(args, "output", "default")

        # For listing, we don't need an identity - just access git
        from pathlib import Path

        class ListSigner(CheckpointSigner):
            """Minimal signer for listing only - doesn't need identity"""

            def __init__(self, git_repo_path=None):
                """Initialize listing-only signer without identity loading."""
                self.git_repo_path = git_repo_path or Path.cwd()
                self.ai_id = "lister"

        # Initialize signer
        signer = ListSigner()

        # List signatures
        signatures = signer.list_signed_checkpoints(session_id=session_id)

        if output_format == "json":
            print(json.dumps({"signatures": signatures, "count": len(signatures)}, indent=2))
        else:
            if not signatures:
                if session_id:
                    print(f"No signed checkpoints found for session: {session_id}")
                else:
                    print("No signed checkpoints found")
                return

            print(f"🔐 Found {len(signatures)} signed checkpoint(s):\n")

            for i, sig in enumerate(signatures, 1):
                print(f"{i}. {sig['session_id'][:12]}... / {sig['phase']} / Round {sig['round']}")
                print(f"   SHA: {sig['checkpoint_sha'][:16]}...")
                print(f"   Signed by: {sig['signed_by']}")
                print(f"   Signed at: {sig['signed_at']}")
                print()

            if session_id:
                print("💡 Verify a checkpoint:")
                first_sig = signatures[0]
                print(
                    f"   empirica checkpoint-verify --session-id {first_sig['session_id']} --phase {first_sig['phase']} --round {first_sig['round']}"
                )

        return signatures

    except Exception as e:
        handle_cli_error(e, "listing signatures")
        sys.exit(1)
