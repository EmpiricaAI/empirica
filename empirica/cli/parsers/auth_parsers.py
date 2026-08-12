"""Parsers for `empirica auth` — OAuth for the proprietary Cortex service.

One group verb with three actions (reduce-CLI-surface rule): login runs the
authorization_code + PKCE browser flow and stores the token set under
cortex.oauth; status reports whether this seat is retirement-ready; logout
revokes and drops the token set (the api_key is never touched).

Cortex is Empirica's PROPRIETARY serving layer (getempirica.com), not part of
this open-source core — connecting requires a Cortex account, which is what
these OAuth verbs authenticate. Empirica core is fully functional without it.
"""

from __future__ import annotations


def add_auth_parsers(subparsers) -> None:
    auth = subparsers.add_parser(
        "auth",
        help=(
            "OAuth for the proprietary Cortex service (getempirica.com; requires an account): "
            "login (browser flow), status (retirement-ready?), logout (revoke)"
        ),
    )
    actions = auth.add_subparsers(dest="auth_action")

    login = actions.add_parser(
        "login",
        help="Authorization_code + PKCE browser flow; stores the token set under cortex.oauth and verifies it with a real authenticated call. Never touches the api_key.",
    )
    login.add_argument(
        "--timeout",
        type=float,
        default=300,
        help="Seconds to wait for the browser callback (optional, default: 300)",
    )
    login.add_argument("--output", choices=["human", "json"], default="human")

    status = actions.add_parser(
        "status",
        help="Show this seat's credential state: oauth token validity, refresh custody, api_key presence, retirement-ready verdict",
    )
    status.add_argument("--output", choices=["human", "json"], default="human")

    logout = actions.add_parser(
        "logout",
        help="Revoke this seat's refresh token at cortex and drop cortex.oauth. The api_key is untouched — logout is never a lockout.",
    )
    logout.add_argument("--output", choices=["human", "json"], default="human")
