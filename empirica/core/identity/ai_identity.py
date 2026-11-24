"""
AI Identity - Ed25519 Keypair Management

Implements cryptographic identity for AI agents using Ed25519 signatures.
Provides keypair generation, secure storage, and identity verification.

Design Principles:
- Ed25519 for fast, secure signatures
- Password-encrypted private key storage (PBKDF2-HMAC-SHA256)
- File system permissions (0600) for private key security
- JSON format for interoperability
- Portable identity (can be exported/imported)

Storage:
- Location: .empirica/identity/<ai_id>.key
- Format: JSON with encrypted private key (PEM format)
- Permissions: 0600 (read/write owner only)
- Encryption: PBKDF2-HMAC-SHA256 with 480000 iterations

Security:
- Private keys are encrypted at rest using password-based encryption
- Password can be provided via EMPIRICA_IDENTITY_PASSWORD environment variable
- If no password is provided, keys are stored unencrypted (development mode only)
"""

import base64
import json
import logging
import os
from datetime import datetime, timezone  # type: ignore[reportAttributeAccessIssue]
from pathlib import Path
from typing import Any

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

logger = logging.getLogger(__name__)

# Password configuration
_IDENTITY_PASSWORD: bytes | None = None


def _get_identity_password() -> bytes | None:
    """Get identity encryption password from environment."""
    global _IDENTITY_PASSWORD
    if _IDENTITY_PASSWORD is None:
        password = os.environ.get("EMPIRICA_IDENTITY_PASSWORD")
        if password:
            _IDENTITY_PASSWORD = password.encode("utf-8")
    return _IDENTITY_PASSWORD


class AIIdentity:
    """
    AI Identity with Ed25519 keypair

    Usage:
        # Create new identity
        identity = AIIdentity(ai_id="claude-code")
        identity.generate_keypair()
        identity.save_keypair()

        # Load existing identity
        identity = AIIdentity(ai_id="claude-code")
        identity.load_keypair()

        # Use for signing
        signature = identity.sign(message)
        verified = identity.verify(signature, message, public_key)
    """

    def __init__(self, ai_id: str, identity_dir: str | None = None):
        """
        Initialize AI Identity

        Args:
            ai_id: AI identifier (e.g., "claude-code", "mini-agent")
            identity_dir: Custom identity storage directory
        """
        self.ai_id = ai_id
        self.identity_dir = Path(identity_dir or ".empirica/identity")
        self.private_key: Ed25519PrivateKey | None = None
        self.public_key: Ed25519PublicKey | None = None
        self.created_at: str | None = None
        self.metadata: dict[str, Any] = {}

    @property
    def keypair_path(self) -> Path:
        """Path to keypair file"""
        return self.identity_dir / f"{self.ai_id}.key"

    @property
    def public_key_path(self) -> Path:
        """Path to public key file (for distribution)"""
        return self.identity_dir / f"{self.ai_id}.pub"

    def generate_keypair(self) -> None:
        """
        Generate new Ed25519 keypair

        Raises:
            RuntimeError: If keypair already exists
        """
        if self.private_key is not None:
            raise RuntimeError(f"Keypair already exists for {self.ai_id}")

        # Generate Ed25519 keypair
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key()
        self.created_at = datetime.now(timezone.utc).isoformat()

        logger.info(f"✓ Generated Ed25519 keypair for {self.ai_id}")

    def save_keypair(self, overwrite: bool = False, password: bytes | None = None) -> None:
        """
        Save keypair to disk with optional encryption.

        Args:
            overwrite: Allow overwriting existing keypair
            password: Encryption password (uses env var EMPIRICA_IDENTITY_PASSWORD if not provided)

        Raises:
            RuntimeError: If no keypair or file exists without overwrite
        """
        if self.private_key is None:
            raise RuntimeError("No keypair to save. Call generate_keypair() first.")

        if self.public_key is None:
            raise RuntimeError("No public key available.")

        if self.keypair_path.exists() and not overwrite:
            raise RuntimeError(f"Keypair already exists at {self.keypair_path}. Use overwrite=True to replace.")

        # Ensure directory exists
        self.identity_dir.mkdir(parents=True, exist_ok=True)

        # Get password from parameter or environment
        effective_password = password or _get_identity_password()

        # Serialize private key with encryption if password available
        if effective_password:
            # Use PEM format with PBKDF2 encryption
            private_pem = self.private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.BestAvailableEncryption(effective_password),
            )
            private_key_data = base64.b64encode(private_pem).decode("ascii")
            encrypted = True
            logger.info("Private key will be encrypted at rest")
        else:
            # Unencrypted (development mode)
            private_bytes = self.private_key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
            private_key_data = private_bytes.hex()
            encrypted = False
            logger.warning("Private key stored UNENCRYPTED. Set EMPIRICA_IDENTITY_PASSWORD for production use.")

        public_bytes = self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )

        # Create keypair JSON
        keypair_data = {
            "ai_id": self.ai_id,
            "created_at": self.created_at,
            "private_key": private_key_data,
            "public_key": public_bytes.hex(),
            "encrypted": encrypted,
            "metadata": self.metadata,
        }

        # Write keypair file
        with open(self.keypair_path, "w") as f:
            json.dump(keypair_data, f, indent=2)

        # Set restrictive permissions (0600)
        os.chmod(self.keypair_path, 0o600)

        # Write public key file (for distribution)
        public_data = {
            "ai_id": self.ai_id,
            "created_at": self.created_at,
            "public_key": public_bytes.hex(),
            "metadata": self.metadata,
        }

        with open(self.public_key_path, "w") as f:
            json.dump(public_data, f, indent=2)

        encryption_status = "encrypted" if encrypted else "unencrypted"
        logger.info(f"✓ Saved keypair ({encryption_status}) to {self.keypair_path}")
        logger.info(f"✓ Saved public key to {self.public_key_path}")

    def load_keypair(self, password: bytes | None = None) -> None:
        """
        Load keypair from disk.

        Args:
            password: Decryption password (uses env var EMPIRICA_IDENTITY_PASSWORD if not provided)

        Raises:
            FileNotFoundError: If keypair file doesn't exist
            ValueError: If decryption fails or AI ID mismatch
        """
        if not self.keypair_path.exists():
            raise FileNotFoundError(
                f"Keypair not found at {self.keypair_path}. "
                f"Create one with: empirica identity-create --ai-id {self.ai_id}"
            )

        # Load keypair JSON
        with open(self.keypair_path) as f:
            keypair_data = json.load(f)

        # Verify ai_id matches
        if keypair_data["ai_id"] != self.ai_id:
            raise ValueError(f"AI ID mismatch: file has {keypair_data['ai_id']}, expected {self.ai_id}")

        # Check if encrypted
        is_encrypted = keypair_data.get("encrypted", False)

        if is_encrypted:
            # Get password from parameter or environment
            effective_password = password or _get_identity_password()
            if not effective_password:
                raise ValueError(
                    "Encrypted keypair requires password. Set EMPIRICA_IDENTITY_PASSWORD environment variable."
                )

            # Decode and decrypt PEM
            private_pem = base64.b64decode(keypair_data["private_key"])
            try:
                loaded_key = serialization.load_pem_private_key(
                    private_pem, password=effective_password, backend=default_backend()
                )
            except Exception as e:
                raise ValueError(f"Failed to decrypt private key: {e}") from e

            # Verify it's an Ed25519 key
            if not isinstance(loaded_key, Ed25519PrivateKey):
                raise ValueError("Loaded key is not an Ed25519 private key")

            self.private_key = loaded_key
            self.public_key = loaded_key.public_key()
            logger.info(f"✓ Loaded encrypted keypair for {self.ai_id}")
        else:
            # Unencrypted (legacy format)
            private_bytes = bytes.fromhex(keypair_data["private_key"])
            public_bytes = bytes.fromhex(keypair_data["public_key"])

            self.private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
            self.public_key = Ed25519PublicKey.from_public_bytes(public_bytes)
            logger.info(f"✓ Loaded unencrypted keypair for {self.ai_id}")
            logger.warning(
                "Keypair is not encrypted. To get an encrypted one, set EMPIRICA_IDENTITY_PASSWORD and run "
                "'empirica identity-create --overwrite' (this replaces the keypair: earlier signatures "
                "will not verify against the new key)."
            )

        self.created_at = keypair_data["created_at"]
        self.metadata = keypair_data.get("metadata", {})

    def sign(self, message: bytes) -> bytes:
        """
        Sign message with private key

        Args:
            message: Message to sign

        Returns:
            bytes: Signature

        Raises:
            RuntimeError: If no private key loaded
        """
        if self.private_key is None:
            raise RuntimeError("No private key loaded. Call load_keypair() first.")

        return self.private_key.sign(message)

    @staticmethod
    def verify(signature: bytes, message: bytes, public_key_bytes: bytes) -> bool:
        """
        Verify signature with public key

        Args:
            signature: Signature to verify
            message: Original message
            public_key_bytes: Public key bytes

        Returns:
            bool: True if signature valid
        """
        try:
            public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
            public_key.verify(signature, message)
            return True
        except Exception:
            return False

    def public_key_hex(self) -> str:
        """Get public key as hex string"""
        if self.public_key is None:
            raise RuntimeError("No public key loaded")

        return self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        ).hex()

    def export_public_key(self) -> dict[str, Any]:
        """
        Export public key for sharing

        Returns:
            Dict with ai_id, public_key, created_at
        """
        if self.public_key is None:
            raise RuntimeError("No public key loaded")

        return {
            "ai_id": self.ai_id,
            "created_at": self.created_at,
            "public_key": self.public_key_hex(),
            "metadata": self.metadata,
        }


class IdentityManager:
    """
    Manage multiple AI identities

    Usage:
        manager = IdentityManager()

        # List identities
        identities = manager.list_identities()

        # Load identity
        identity = manager.load_identity("claude-code")

        # Create identity
        identity = manager.create_identity("new-agent")
    """

    def __init__(self, identity_dir: str | None = None):
        """Initialize identity manager"""
        self.identity_dir = Path(identity_dir or ".empirica/identity")

    def list_identities(self) -> list[dict[str, Any]]:
        """
        List all identities

        Returns:
            List of identity summaries
        """
        if not self.identity_dir.exists():
            return []

        identities = []

        for key_file in self.identity_dir.glob("*.key"):
            try:
                with open(key_file) as f:
                    data = json.load(f)

                identities.append(
                    {
                        "ai_id": data["ai_id"],
                        "created_at": data["created_at"],
                        "key_file": str(key_file),
                        "has_public_key": (self.identity_dir / f"{data['ai_id']}.pub").exists(),
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to load {key_file}: {e}")

        return identities

    def load_identity(self, ai_id: str) -> AIIdentity:
        """
        Load existing identity

        Args:
            ai_id: AI identifier

        Returns:
            AIIdentity: Loaded identity
        """
        identity = AIIdentity(ai_id, str(self.identity_dir))
        identity.load_keypair()
        return identity

    def create_identity(self, ai_id: str, overwrite: bool = False) -> AIIdentity:
        """
        Create new identity

        Args:
            ai_id: AI identifier
            overwrite: Allow overwriting existing identity

        Returns:
            AIIdentity: New identity
        """
        identity = AIIdentity(ai_id, str(self.identity_dir))
        identity.generate_keypair()
        identity.save_keypair(overwrite=overwrite)
        return identity

    def identity_exists(self, ai_id: str) -> bool:
        """Check if identity exists"""
        return (self.identity_dir / f"{ai_id}.key").exists()
