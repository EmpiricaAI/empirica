"""Cortex OAuth for CLI-owned seats — the authorization_code path.

The CLI acquires and renews its OWN tokens (own DCR client, sole refresher),
which is what makes api_key retirement structurally possible on a CLI box.
The brokered extension-write path cannot deliver retirement (its fallback is
load-bearing); this path can.
"""

from .cortex_oauth import (
    build_pkce,
    cortex_bearer,
    default_refresh,
    exchange_code,
    fetch_discovery,
    login,
    refresh_access_token,
    register_client,
)
from .credential_health import (
    dead_reason,
    parse_unauthorized,
)
from .credential_health import (
    mark as mark_credential_dead,
)

__all__ = [
    "build_pkce",
    "cortex_bearer",
    "dead_reason",
    "default_refresh",
    "exchange_code",
    "fetch_discovery",
    "login",
    "mark_credential_dead",
    "parse_unauthorized",
    "refresh_access_token",
    "register_client",
]
