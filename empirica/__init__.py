"""
Empirica - Epistemic Vector-Based Functional Self-Awareness Framework

A production-ready system for AI epistemic self-awareness and reasoning validation.

Core Philosophy: "Measure and validate without interfering"

Key Features:
- 13D epistemic vectors (know, uncertainty, context, clarity, coherence, etc.)
- CASCADE workflow: PREFLIGHT → CHECK → POSTFLIGHT
- Git-integrated reflex logging
- Session database (SQLite) with breadcrumb tracking
- Drift detection and signaling

Version: 1.8.16
"""

__version__ = "1.8.16"
__author__ = "Empirica Project"

# Lazy imports — heavy modules (git, cryptography, jsonschema) are only
# loaded when actually accessed, not when any empirica.* submodule is imported.
# This drops import time from ~113ms to ~2ms for lightweight consumers
# like the statusline script that only need path_resolver or signaling.

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from empirica.core.canonical import GitEnhancedReflexLogger
    from empirica.data.session_database import SessionDatabase


def __getattr__(name):
    if name == "GitEnhancedReflexLogger":
        from empirica.core.canonical import GitEnhancedReflexLogger
        return GitEnhancedReflexLogger
    if name == "SessionDatabase":
        from empirica.data.session_database import SessionDatabase
        return SessionDatabase
    raise AttributeError(f"module 'empirica' has no attribute {name!r}")


__all__ = [
    'GitEnhancedReflexLogger',
    'SessionDatabase',
]
