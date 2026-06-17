"""
SignedGitOperations - Store and Verify Signed Epistemic States in Git

Enables storing epistemic states as cryptographically signed data in Git notes,
creating an immutable audit trail of AI reasoning. Each CASCADE phase is stored
with a signature that can be verified later.

Key Design:
1. Each phase commit stores signed epistemic state in git notes
2. Commit author is set to the persona (for accountability)
3. Signatures verify with persona's public key
4. Can replay entire CASCADE trace and verify at each step
5. Perfect for regulatory compliance and reproducible research

Flow:
    1. Sign epistemic state with persona's private key
    2. Store signed state in git notes
    3. Create commit with persona as author
    4. Later: verify signature chain for audit trail
"""

import json
import logging
import os
import subprocess

# Import GitPython - handle naming conflict with empirica.core.git package
# We import git commands but need to be careful of the package name conflict
import sys
from datetime import datetime, timezone  # type: ignore[reportAttributeAccessIssue]
from pathlib import Path
from typing import Any

# GitPython is a heavy import (~140ms). It is only needed when an actual git
# operation runs (commit/verify/git-notes I/O), never just to import this
# module. We load it lazily so that `import empirica.cli` — which transitively
# imports this module via the command-handler re-export shim — does not pay the
# GitPython cost for commands that never touch git (e.g. goals-list).
#
# The names GitRepo, GitCommandError and GIT_PYTHON_AVAILABLE resolve on first
# access: externally via PEP 562 module __getattr__, internally via the
# _load_gitpython() call at the top of SignedGitOperations.__init__ (the only
# entry point through which git operations are reached).
_GIT_LOADED = False


def _load_gitpython() -> None:
    """Import GitPython on demand and publish the module-level names. Idempotent."""
    global _GIT_LOADED, GitRepo, GitCommandError, GIT_PYTHON_AVAILABLE
    if _GIT_LOADED:
        return
    # Temporarily remove empirica.core.git so the import resolves the real
    # GitPython 'git' module rather than our package sharing the leaf name.
    _empirica_git_module = sys.modules.pop('empirica.core.git', None)
    try:
        import git
        GitRepo = git.Repo
        GitCommandError = git.GitCommandError
        GIT_PYTHON_AVAILABLE = True
    except ImportError:
        # GitPython not installed - use fallback
        GitRepo = None
        GitCommandError = Exception
        GIT_PYTHON_AVAILABLE = False
    finally:
        if _empirica_git_module is not None:
            sys.modules['empirica.core.git'] = _empirica_git_module
    _GIT_LOADED = True


def __getattr__(name: str):
    """PEP 562 lazy access for the GitPython-backed module names."""
    if name in ("GitRepo", "GitCommandError", "GIT_PYTHON_AVAILABLE"):
        _load_gitpython()
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

from empirica.core.persona.signing_persona import SigningPersona  # noqa: E402 — after sys.modules guard

logger = logging.getLogger(__name__)


class SignedGitOperations:
    """
    Store and verify signed epistemic states in Git

    Creates an immutable, verifiable audit trail of AI reasoning by:
    1. Signing epistemic states with persona's Ed25519 key
    2. Storing signatures in git notes
    3. Making commits with persona's identity
    4. Verifying entire CASCADE chains

    Usage:
        repo = git.Repo(".")
        git_ops = SignedGitOperations(repo)

        # Sign and store epistemic state
        state = {...13 vectors...}
        commit_sha = git_ops.commit_signed_state(
            signing_persona=persona,
            epistemic_state=state,
            phase="PREFLIGHT",
            message="Starting new task"
        )

        # Later: verify entire CASCADE chain
        results = git_ops.verify_cascade_chain(
            start_commit="abc123",
            end_commit="def456"
        )

        for result in results:
            print(f"Phase {result['phase']}: verified={result['state_verified']}")
    """

    def __init__(self, repo_path: str = ".", enforce_cascade_phases: bool = False):
        """
        Initialize Git operations

        Args:
            repo_path: Path to git repository (default: current dir)
            enforce_cascade_phases: If True, enforce CASCADE phase ordering and validation
                                   Can also be set via EMPIRICA_ENFORCE_CASCADE_PHASES env var

        Raises:
            git.InvalidGitRepositoryError: If path is not a git repo
            ImportError: If GitPython not installed
        """
        # Resolve GitPython lazily on first construction (see module header).
        _load_gitpython()
        if not GIT_PYTHON_AVAILABLE:
            raise ImportError("GitPython not installed - git operations unavailable. Install with: pip install gitpython")

        if GitRepo is None:
            raise ImportError("GitPython not installed")
        self.repo = GitRepo(repo_path)
        self.git = self.repo.git

        # Check environment variable for enforcement
        env_enforce = os.getenv("EMPIRICA_ENFORCE_CASCADE_PHASES", "").lower() in ("true", "1", "yes")
        self.enforce_cascade_phases = enforce_cascade_phases or env_enforce

        mode_str = "with CASCADE enforcement" if self.enforce_cascade_phases else "without enforcement"
        logger.info(f"✓ Initialized SignedGitOperations for {repo_path} {mode_str}")

    def commit_signed_state(
        self,
        signing_persona: SigningPersona,
        epistemic_state: dict[str, float],
        phase: str,
        message: str,
        additional_data: dict[str, Any] | None = None,
        required_personas: list[str] | None = None
    ) -> str:
        """
        Create a Git commit with signed epistemic state in notes

        Flow:
        1. Sign the epistemic state with persona's private key
        2. Store signed state in git notes
        3. Create commit with persona as author
        4. Return commit SHA

        Args:
            signing_persona: SigningPersona with keypair loaded
            epistemic_state: Dict with 13 epistemic vectors
            phase: CASCADE phase name (PREFLIGHT, INVESTIGATE, etc.)
            message: Commit message
            additional_data: Optional metadata to include
            required_personas: List of personas required to sign (for enforcement)

        Returns:
            str: Commit SHA hash

        Raises:
            ValueError: If invalid epistemic state or phase enforcement fails
            GitCommandError: If git command fails
        """
        # Validate CASCADE phases if enforcement is enabled
        if self.enforce_cascade_phases:
            self._validate_cascade_phase(phase, required_personas)

        # Sign the epistemic state
        signed_state = signing_persona.sign_epistemic_state(
            epistemic_state,
            phase=phase,
            additional_data=additional_data
        )

        # Prepare signed state for git notes
        signed_json = json.dumps(signed_state, indent=2)

        try:
            # Create commit with persona as author
            persona_info = signing_persona.get_persona_info()
            author_name = persona_info["persona_id"]
            author_email = f"{persona_info['persona_id']}@empirica.local"

            # Extract impact and completion for commit message tag
            impact = epistemic_state.get('impact', 0.5)
            completion = epistemic_state.get('completion', 0.0)

            # Create signed commit with epistemic tags
            commit_message = f"[{phase}] {message} [impact={impact:.2f}, completion={completion:.2f}]\n\nPersona: {persona_info['name']}\nVersion: {persona_info['version']}"

            # Use git command directly for --allow-empty (not supported by GitPython)
            subprocess.run(
                [
                    'git', 'commit',
                    '--allow-empty',
                    '-m', commit_message,
                    '--author', f'{author_name} <{author_email}>'
                ],
                cwd=self.repo.working_dir,
                check=True,
                capture_output=True,
                text=True
            )

            # Refresh repo state and get commit SHA
            self.repo.head.reset(commit=self.repo.head.commit, index=True)
            commit_sha = self.repo.head.commit.hexsha

            # Add signed state to git notes AFTER commit exists
            # This attaches the note to the correct commit
            self.git.notes(
                'add',
                '--force',  # Overwrite if exists
                '-m', signed_json,
                commit_sha  # Explicitly attach to this commit
            )

            logger.info(
                f"✓ Committed signed state: {phase} "
                f"SHA={commit_sha[:7]} "
                f"persona={author_name}"
            )

            return commit_sha

        except subprocess.CalledProcessError as e:
            logger.error(f"Git command failed: {e.stderr}")
            raise GitCommandError("git notes add", e.returncode, e.stderr) from e

    def get_signed_state_from_commit(self, commit_sha: str) -> dict[str, Any] | None:
        """
        Retrieve signed epistemic state from commit's git notes

        Args:
            commit_sha: Commit SHA hash

        Returns:
            Dict with signed state, or None if no notes found
        """
        try:
            # Get notes for this commit
            note = self.git.notes('show', commit_sha)

            # Parse as JSON
            signed_state = json.loads(note)
            return signed_state

        except (GitCommandError, json.JSONDecodeError, ValueError):
            return None

    def verify_cascade_chain(
        self,
        start_commit: str,
        end_commit: str,
    ) -> list[dict[str, Any]]:
        """
        Verify entire CASCADE trace from start to end commit

        For each commit in the range:
        1. Get commit metadata
        2. Load signed state from git notes
        3. Verify signature with persona's public key
        4. Return verification results

        Args:
            start_commit: Starting commit SHA
            end_commit: Ending commit SHA

        Returns:
            List[Dict] with verification results for each commit:
                - commit: Commit SHA
                - phase: CASCADE phase
                - author: Persona ID
                - timestamp: Commit timestamp
                - gpg_verified: Whether commit is GPG signed (if applicable)
                - state_verified: Whether epistemic state signature is valid
                - persona_id: Who signed it
                - state_timestamp: When state was signed
                - vectors_preview: Sample of epistemic vectors

        """
        results = []

        try:
            # Get commits in range (use start^..end for inclusive range)
            # If start has no parent (initial commit), fall back to listing all to end
            try:
                start_obj = self.repo.commit(start_commit)
                if start_obj.parents:
                    # Has parent - use parent..end to include start
                    commits = list(self.repo.iter_commits(f"{start_commit}^..{end_commit}"))
                else:
                    # No parent (initial commit) - list all commits up to end
                    commits = list(self.repo.iter_commits(end_commit))
            except Exception:
                # Fallback to exclusive range
                commits = list(self.repo.iter_commits(f"{start_commit}..{end_commit}"))

            logger.info(f"Verifying CASCADE chain: {len(commits)} commits")

            for commit in commits:
                verification = {
                    "commit": commit.hexsha[:7],
                    "commit_full": commit.hexsha,
                    "author": commit.author.name,
                    "message": commit.message.split('\n')[0],  # First line only
                    "timestamp": commit.committed_datetime.isoformat(),
                    "gpg_verified": None,
                    "state_verified": False,
                    "state": None,
                    "signature_valid": False,
                    "phase": None,
                    "persona_id": None,
                    "error": None
                }

                # Try to get signed state from git notes
                signed_state = self.get_signed_state_from_commit(commit.hexsha)

                if signed_state is None:
                    verification["error"] = "No signed state in git notes"
                    results.append(verification)
                    continue

                try:
                    state_data = signed_state.get("state", {})
                    verification["phase"] = state_data.get("phase")
                    verification["persona_id"] = state_data.get("persona_id")
                    verification["state_timestamp"] = state_data.get("timestamp")

                    # Preview of epistemic vectors
                    vectors = state_data.get("vectors", {})
                    verification["vectors_preview"] = {
                        "know": vectors.get("know"),
                        "uncertainty": vectors.get("uncertainty"),
                        "engagement": vectors.get("engagement")
                    }

                    # Verify signature
                    public_key_hex = state_data.get("public_key")

                    if public_key_hex:
                        # Verify using Ed25519
                        signature_hex = signed_state.get("signature", "")

                        try:
                            # Recreate the message that was signed
                            message_json = json.dumps(state_data, sort_keys=True, separators=(',', ':'))
                            message_bytes = message_json.encode('utf-8')
                            signature_bytes = bytes.fromhex(signature_hex)
                            public_key_bytes = bytes.fromhex(public_key_hex)

                            # Verify signature
                            from empirica.core.identity.ai_identity import AIIdentity
                            is_valid = AIIdentity.verify(
                                signature_bytes,
                                message_bytes,
                                public_key_bytes
                            )

                            verification["state_verified"] = is_valid
                            verification["signature_valid"] = is_valid

                            if is_valid:
                                logger.info(
                                    f"✓ Verified: {verification['commit']} "
                                    f"phase={verification['phase']} "
                                    f"persona={verification['persona_id']}"
                                )
                            else:
                                logger.warning(
                                    f"✗ Signature invalid: {verification['commit']} "
                                    f"phase={verification['phase']}"
                                )

                        except Exception as e:
                            verification["error"] = f"Signature verification error: {e!s}"

                except Exception as e:
                    verification["error"] = f"State parsing error: {e!s}"

                results.append(verification)

            return results

        except GitCommandError as e:
            logger.error(f"Git command failed: {e}")
            raise

    def export_cascade_report(
        self,
        start_commit: str,
        end_commit: str,
        output_file: str | None = None
    ) -> dict[str, Any]:
        """
        Export CASCADE chain verification report

        Creates a JSON report of all verifications that can be:
        - Stored for audit purposes
        - Shared with collaborators
        - Used for reproducibility verification

        Args:
            start_commit: Starting commit SHA
            end_commit: Ending commit SHA
            output_file: Optional file to write report to

        Returns:
            Dict with complete verification report
        """
        # Get verification results
        results = self.verify_cascade_chain(start_commit, end_commit)

        # Create report
        report = {
            "title": "Empirica CASCADE Verification Report",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "start_commit": start_commit,
            "end_commit": end_commit,
            "repository": str(self.repo.working_dir),
            "total_commits": len(results),
            "verified_commits": sum(1 for r in results if r.get("state_verified")),
            "unverified_commits": sum(1 for r in results if not r.get("state_verified")),
            "commits": results,
            "summary": {
                "all_verified": all(r.get("state_verified") for r in results),
                "phases_covered": list({r.get("phase") for r in results if r.get("phase")}),
                "personas_involved": list({r.get("persona_id") for r in results if r.get("persona_id")})
            }
        }

        # Write to file if requested
        if output_file:
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'w') as f:
                json.dump(report, f, indent=2)

            logger.info(f"✓ Wrote CASCADE report to {output_file}")

        return report

    def _validate_cascade_phase(
        self,
        phase: str,
        required_personas: list[str] | None = None
    ) -> None:
        """
        Validate CASCADE phase ordering and requirements

        Args:
            phase: CASCADE phase (PREFLIGHT, INVESTIGATE, CHECK, ACT, POSTFLIGHT)
            required_personas: List of personas that must sign this phase

        Raises:
            ValueError: If phase validation fails
        """
        CASCADE_ORDER = ["PREFLIGHT", "INVESTIGATE", "CHECK", "ACT", "POSTFLIGHT"]

        if phase not in CASCADE_ORDER:
            raise ValueError(f"Invalid CASCADE phase: {phase}. Must be one of: {CASCADE_ORDER}")

        # Get last phase from git log
        last_phase = self._get_last_cascade_phase()

        if last_phase:
            last_idx = CASCADE_ORDER.index(last_phase)
            current_idx = CASCADE_ORDER.index(phase)

            if current_idx <= last_idx:
                raise ValueError(
                    f"Phase {phase} cannot follow {last_phase}. "
                    f"CASCADE must progress: {' → '.join(CASCADE_ORDER)}"
                )

        if required_personas:
            # This would check that all required personas have signed this phase
            # Implementation depends on CASCADE orchestration logic
            logger.info(f"Phase {phase} requires sign-off from: {required_personas}")

        logger.info(f"✓ CASCADE phase validation passed: {phase}")

    def _get_last_cascade_phase(self) -> str | None:
        """
        Get the last CASCADE phase that was committed

        Returns:
            Last phase string or None if no phases committed yet
        """
        try:
            # Search git log for phase markers in commit messages
            log = self.git.log("--oneline", "--all", "-20")
            lines = log.split('\n')

            CASCADE_ORDER = ["PREFLIGHT", "INVESTIGATE", "CHECK", "ACT", "POSTFLIGHT"]

            for line in lines:
                for phase in CASCADE_ORDER:
                    if f"[{phase}]" in line:
                        return phase

            return None
        except Exception as e:
            logger.warning(f"Could not determine last CASCADE phase: {e}")
            return None

    def get_cascade_timeline(
        self,
        start_commit: str,
        end_commit: str
    ) -> list[dict[str, Any]]:
        """
        Get epistemic timeline for CASCADE phases

        Returns chronological sequence of epistemic states through CASCADE

        Args:
            start_commit: Starting commit
            end_commit: Ending commit

        Returns:
            List of epistemic snapshots in chronological order
        """
        results = self.verify_cascade_chain(start_commit, end_commit)

        # Filter to only verified states and sort by timestamp
        timeline = [
            {
                "phase": r.get("phase"),
                "timestamp": r.get("state_timestamp"),
                "persona": r.get("persona_id"),
                "vectors": r.get("vectors_preview"),
                "commit": r.get("commit")
            }
            for r in results
            if r.get("state_verified")
        ]

        # Sort by timestamp
        timeline.sort(key=lambda x: x.get("timestamp", ""))

        return timeline
