"""Parsers for `empirica auth` — OAuth for the proprietary Cortex service.

One group verb with four actions (reduce-CLI-surface rule): login runs the
authorization_code + PKCE browser flow and stores the token set under
cortex.oauth; token prints a currently valid access token from that set, for
other ecosystem tools; status reports whether this seat is retirement-ready;
logout revokes and drops the token set (the api_key is never touched).

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
            "login (browser flow), token (for other tools), status (retirement-ready?), logout (revoke)"
        ),
    )
    actions = auth.add_subparsers(dest="auth_action")

    login = actions.add_parser(
        "login",
        help="Authorization_code + PKCE browser flow; stores the token set under cortex.oauth and verifies it with a real authenticated call. Drops a leftover api_key (retired).",
    )
    login.add_argument(
        "--timeout",
        type=float,
        default=300,
        help="Seconds to wait for the browser callback (optional, default: 300)",
    )
    login.add_argument(
        "--url",
        help="The Cortex to sign in to (optional; default: cortex.url in credentials.yaml, else the hosted "
        "https://cortex.getempirica.com). Saved after a successful sign-in.",
    )
    login.add_argument(
        "--invite",
        metavar="TOKEN_OR_LINK",
        help="Join an org with the invite its admin sent: the token, or the whole "
        "https://getempirica.com/install?invite=... link (optional). Cortex redeems it once your Google email matches.",
    )
    login.add_argument(
        "--org",
        metavar="ORG",
        help="Sign in as your user in this org (its id, slug or name), with no picker (optional). Refused if you are "
        "not a member there.",
    )
    login.add_argument(
        "--remote",
        action="store_true",
        help="For a machine whose browser is elsewhere (SSH, a server): prints a URL to open on any machine, then reads "
        "the code Cortex's page shows (code#state) pasted back here. No local port is used. Chosen by itself over SSH "
        "(SSH_CONNECTION or SSH_TTY, also in tmux's session environment) and on Linux with no display (optional).",
    )
    login.add_argument(
        "--local",
        action="store_true",
        help="Use this machine's browser and a local callback even where --remote would be chosen by itself (optional).",
    )
    login.add_argument("--output", choices=["human", "json"], default="human")

    token = actions.add_parser(
        "token",
        help=(
            "Print a currently valid access token from the `auth login` token set, refreshing it if it has "
            "expired, so other ecosystem tools present the same credential instead of a static key. Exits 1 "
            "with no token printed when there is none or it cannot be refreshed."
        ),
    )
    token.add_argument(
        "--org",
        metavar="ORG_ID",
        help="The team whose token to print (its org id); default: the default sign-in (optional).",
    )
    token.add_argument("--output", choices=["human", "json"], default="human")
    token.add_argument(
        "--headers",
        action="store_true",
        help=(
            'Print the HTTP header object Claude Code\'s `headersHelper` expects, {"Authorization": "Bearer <token>"}, '
            "built as JSON (no shell quoting to get wrong). On any failure nothing is printed to stdout, so the "
            "connection fails visibly instead of presenting an empty bearer."
        ),
    )

    status = actions.add_parser(
        "status",
        help="Show this seat's credential state: oauth token validity, refresh custody, api_key presence, retirement-ready verdict",
    )
    status.add_argument("--output", choices=["human", "json"], default="human")

    connectors = actions.add_parser(
        "connectors",
        help=(
            "List MCP connectors in ~/.claude.json that store a static Authorization bearer and switch the ones "
            "you name to `headersHelper: empirica auth token --headers`, so nothing is stored. Dry run unless "
            "--apply; backs the file up first; only hosts that are ours; only with a valid OAuth token."
        ),
    )
    connectors.add_argument("--name", action="append", default=[], help="Connector to switch (repeatable)")
    connectors.add_argument("--apply", action="store_true", help="Write the change (default: show the plan only)")
    connectors.add_argument("--output", choices=["human", "json"], default="human")

    logout = actions.add_parser(
        "logout",
        help="Revoke this seat's refresh token at cortex and drop cortex.oauth. After logout this seat has no Cortex credential until the next `empirica auth login`.",
    )
    logout.add_argument(
        "--org",
        metavar="ORG_ID",
        help="Log out of this team only (its org id); others stay signed in. Default: the default sign-in (optional).",
    )
    logout.add_argument("--output", choices=["human", "json"], default="human")
