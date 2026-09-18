"""
Identity Management CLI Commands

Handles cryptographic identity operations for AI agents:
- identity-create: Generate new keypair
- identity-list: List all identities
- identity-export: Export public key for sharing

Phase 2: Cryptographic Trust Layer (EEP-1)
"""

import json
import logging
from pathlib import Path

from ..cli_utils import handle_cli_error

logger = logging.getLogger(__name__)


def handle_identity_create_command(args):
    """Create new AI identity with Ed25519 keypair"""
    try:
        from empirica.core.identity import IdentityManager

        ai_id = args.ai_id
        overwrite = getattr(args, "overwrite", False)

        # Check if identity exists
        manager = IdentityManager()
        if manager.identity_exists(ai_id) and not overwrite:
            result = {
                "ok": False,
                "error": f"Identity '{ai_id}' already exists",
                "message": "Use --overwrite to replace existing identity",
                "key_file": str(Path(".empirica/identity") / f"{ai_id}.key"),
            }

            if hasattr(args, "output") and args.output == "json":
                print(json.dumps(result, indent=2))
            else:
                print(f"❌ Identity '{ai_id}' already exists")
                print(f"   Key file: {result['key_file']}")
                print(f"\n💡 To replace: empirica identity-create --ai-id {ai_id} --overwrite")

            # Return None to avoid exit code issues and duplicate output
            return None

        # Create identity
        identity = manager.create_identity(ai_id, overwrite=overwrite)

        result = {
            "ok": True,
            "ai_id": ai_id,
            "public_key": identity.public_key_hex(),
            "created_at": identity.created_at,
            "key_file": str(identity.keypair_path),
            "public_key_file": str(identity.public_key_path),
            "message": "Identity created successfully",
        }

        # Format output
        if hasattr(args, "output") and args.output == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"✅ Identity created: {ai_id}")
            print("\n🔑 Public Key:")
            print(f"   {identity.public_key_hex()}")
            print("\n📁 Files:")
            print(f"   Private key: {identity.keypair_path}")
            print(f"   Public key:  {identity.public_key_path}")
            print("\n🔒 Security:")
            print("   • Private key permissions: 0600 (owner read/write only)")
            print("   • Keep private key secure!")
            print("   • Share public key freely")
            print("\n💡 Next steps:")
            print(
                f"   1. Sign a checkpoint: empirica checkpoint-sign --session-id <id> --phase PREFLIGHT --round 1 --ai-id {ai_id}"
            )
            print("   2. Verify it: empirica checkpoint-verify --session-id <id> --phase PREFLIGHT --round 1")
            print(f"   3. Share public key: empirica identity-export --ai-id {ai_id}")

        # Return None to avoid exit code issues and duplicate output
        return None

    except Exception as e:
        handle_cli_error(e, "Identity creation", getattr(args, "verbose", False))
        # Error handler already manages output, return None to avoid duplicate output
        return None


def handle_identity_list_command(args):
    """List all AI identities"""
    try:
        from empirica.core.identity import IdentityManager

        manager = IdentityManager()
        identities = manager.list_identities()

        result = {"ok": True, "count": len(identities), "identities": identities}

        # Format output
        if hasattr(args, "output") and args.output == "json":
            print(json.dumps(result, indent=2))
        else:
            if not identities:
                print("🔍 No identities found")
                print("\n💡 Create one with:")
                print("   empirica identity-create --ai-id <name>")
            else:
                print(f"🔑 Found {len(identities)} identit{'y' if len(identities) == 1 else 'ies'}:\n")

                for i, identity in enumerate(identities, 1):
                    print(f"{i}. {identity['ai_id']}")
                    print(f"   Created: {identity['created_at'][:10]}")
                    print(f"   Key file: {identity['key_file']}")

                    if identity["has_public_key"]:
                        print("   Public key: ✓")
                    else:
                        print("   Public key: ✗ (missing)")

                    print()

                print("💡 Commands:")
                print("   • Export public key: empirica identity-export --ai-id <name>")
                print('   • Use for signing: empirica preflight "task" --ai-id <name> --sign')

        # Return None to avoid exit code issues and duplicate output
        return None

    except Exception as e:
        handle_cli_error(e, "Identity list", getattr(args, "verbose", False))
        # Error handler already manages output, return None to avoid duplicate output
        return None


def handle_identity_export_command(args):
    """Export public key for sharing"""
    try:
        from empirica.core.identity import IdentityManager

        ai_id = args.ai_id

        manager = IdentityManager()
        identity = manager.load_identity(ai_id)

        public_key_data = identity.export_public_key()

        result = {"ok": True, **public_key_data}

        # Format output
        if hasattr(args, "output") and args.output == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"📤 Public Key Export: {ai_id}\n")
            print("Public Key:")
            print(f"{public_key_data['public_key']}\n")
            print(f"Created: {public_key_data['created_at']}")

            if public_key_data.get("metadata"):
                print("\nMetadata:")
                for key, value in public_key_data["metadata"].items():
                    print(f"  {key}: {value}")

            print("\n💡 Share this public key:")
            print("   • Others can verify your signed assessments")
            print("   • Public key is safe to distribute")
            print("   • Never share your private key!")

        # Return None to avoid exit code issues and duplicate output
        return None

    except FileNotFoundError:
        result = {
            "ok": False,
            "error": f"Identity '{ai_id}' not found",
            "message": "Create identity first with: empirica identity-create --ai-id <name>",
        }

        if hasattr(args, "output") and args.output == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"❌ Identity '{ai_id}' not found")
            print("\n💡 Create it first:")
            print(f"   empirica identity-create --ai-id {ai_id}")

        # Return None to avoid exit code issues and duplicate output
        return None

    except Exception as e:
        handle_cli_error(e, "Identity export", getattr(args, "verbose", False))
        # Error handler already manages output, return None to avoid duplicate output
        return None
