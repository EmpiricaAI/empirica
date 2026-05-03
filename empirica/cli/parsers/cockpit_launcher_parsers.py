"""Argparse parser for ``empirica cockpit <action>`` (launcher v1).

Per ``docs/specs/PROPOSAL_COCKPIT_LAUNCHER.md``. Adds a ``cockpit``
subcommand group with ``launch / status / detach / kill`` actions.
"""

from __future__ import annotations


def add_cockpit_launcher_parsers(subparsers):
    """Register the ``cockpit`` verb group on the top-level subparsers."""
    cockpit_root = subparsers.add_parser(
        'cockpit',
        help='Multi-instance cockpit launcher — bring up the canonical '
             'tmux layout in one command, with abnormal-exit detection',
        description="""
Single-command bring-up of a configured multi-Claude tmux cockpit
(one window per project, optional status windows for monitoring).

Layout-only — Claude conversations regenerate on each launch (by design;
that's `/compact` + Empirica artifacts' job, not the launcher's).

State files live under ~/.empirica/cockpit/:
  config.yaml           — your project list + status windows
  last_session_start    — mtime of most recent `cockpit launch`
  last_clean_shutdown   — mtime of most recent graceful `kill` / `detach`
  active.lock           — PID of the running cockpit (cleared on clean exit)

Abnormal-exit detection compares the timestamps + checks the lock PID:
when the previous session ended without writing a clean-shutdown marker
(reboot, OOM, forced kill), the next `cockpit launch` warns and suggests
`empirica instance prune`.
        """,
    )
    actions = cockpit_root.add_subparsers(
        dest='cockpit_action', metavar='action',
    )

    # launch
    launch = actions.add_parser(
        'launch',
        help='Bring up the cockpit (idempotent — attaches if already running)',
    )
    launch.add_argument('--config', metavar='PATH',
                        help='Override the default config path '
                             '(~/.empirica/cockpit/config.yaml)')
    launch.add_argument('--no-attach', action='store_true',
                        help="Don't attach after creating the layout — useful "
                             "for headless / scripted bring-up")
    launch.add_argument('--quiet-warnings', action='store_true',
                        help='Suppress the abnormal-exit warning even when '
                             'the previous session ended uncleanly')
    launch.add_argument('--surface', choices=['tmux', 'alacritty'],
                        help='Override the surface from config. tmux = legacy '
                             'single-attach. alacritty = one alacritty window per '
                             'group with WM_CLASS for KDE Meta+1..N switching '
                             '(requires "groups:" in config).')
    launch.add_argument('--output', choices=['human', 'json'], default='human',
                        help='Output format')

    # status
    status = actions.add_parser(
        'status',
        help='Show current cockpit state without attaching (read-only)',
    )
    status.add_argument('--config', metavar='PATH',
                        help='Override the default config path')
    status.add_argument('--output', choices=['human', 'json'], default='human',
                        help='Output format')

    # detach
    detach = actions.add_parser(
        'detach',
        help='Clean detach: write the clean-shutdown marker + tmux detach-client',
    )
    detach.add_argument('--output', choices=['human', 'json'], default='human',
                        help='Output format')

    # kill
    kill = actions.add_parser(
        'kill',
        help='Destroy the cockpit session and write clean-shutdown marker',
    )
    kill.add_argument('--config', metavar='PATH',
                      help='Override the default config path')
    kill.add_argument('--prune', action='store_true',
                      help='Also prune dead per-instance state files '
                           '(equivalent to `empirica instance prune`)')
    kill.add_argument('--output', choices=['human', 'json'], default='human',
                      help='Output format')
