"""Argparse parser for ``empirica cockpit <action>`` (launcher v1).

Per ``docs/specs/PROPOSAL_COCKPIT_LAUNCHER.md``. Adds a ``cockpit``
subcommand group with ``launch / refresh / status / detach / kill`` actions.

The help here is the short form of docs/guides/COCKPIT.md. tests/test_cockpit_and_type_help.py keeps the facts a
person needs first (profiles, surfaces, the status check, refresh, identity) in it.
"""

from __future__ import annotations

import argparse

_PROFILE_HELP = (
    "Use the profile NAME: the file ~/.empirica/cockpit/config-NAME.yaml. Without it the default "
    "~/.empirica/cockpit/config.yaml is used. An unknown name is refused and the error lists the profiles that exist"
)
_CONFIG_HELP = "Use this config file instead of the default or a profile"

_ROOT_DESCRIPTION = """\
A cockpit is one tmux session that holds your practices: one window per group,
one pane per practice, each started in its own directory with a stable identity.
A config file describes the layout. Guide: docs/guides/COCKPIT.md

Profiles (--profile NAME)
  The default layout is ~/.empirica/cockpit/config.yaml. A second layout is a
  second file beside it, config-NAME.yaml, selected with --profile NAME:
    ~/.empirica/cockpit/config-NAME.yaml
  Give each profile its own `session_name:`, since each is its own tmux session.
  An unknown name is refused with the list of profiles that exist. After editing
  a profile by hand, run `empirica cockpit status --profile NAME`: it checks the
  file, changes nothing, and exits 1 with the problem if there is one.

Surfaces (`surface:` in the config, or `launch --surface`)
  tmux       the layout opens in the terminal you launched from. This is the one
             that works over SSH and on a machine with no display.
  alacritty  one new alacritty window on this machine's display.
  ghostty    one new ghostty window on this machine's display.

The bars
  Your practices are on top, with health on the right. The commands are on the
  line under each pane, red when something is broken and orange when something
  is waiting. Click that line for the command menu, or press Ctrl+b, then e,
  then a letter (Space for the menu, ? for help). `bar: false` turns them off.

Identity and resume
  Each practice pane gets EMPIRICA_INSTANCE_ID: the project's `name:`, or its
  `instance_id:` if set. Transactions, goals and calibration follow that id, so
  a practice that is restarted is the same practice. `refresh` restarts a pane
  whose claude, codex or ecodex exited, in place, and resumes its conversation
  (`claude --continue`, or the project's `resume:` command).

State files, under ~/.empirica/cockpit/
  config.yaml           the default layout; config-NAME.yaml for each profile
  last_session_start    time of the most recent `cockpit launch`
  last_clean_shutdown   time of the most recent `kill` or `detach`
  active.lock           PID of the running cockpit (cleared on a clean exit)
  When the last cockpit ended without `kill` or `detach` (a reboot, an OOM
  kill), the next `launch` says so and suggests `empirica instance prune`.
"""

_ROOT_EPILOG = """\
examples:
  empirica cockpit launch                        bring up the default layout and attach
  empirica cockpit launch --profile monitor-b    bring up ~/.empirica/cockpit/config-monitor-b.yaml
  empirica cockpit status --profile monitor-b    check that profile; changes nothing
  empirica cockpit refresh                       bring back every practice pane that exited
  empirica cockpit detach                        leave it running and record a clean shutdown
  empirica cockpit kill --prune                  end it and remove state of instances that are gone
"""


def _action(actions, name: str, summary: str, description: str, examples: str) -> argparse.ArgumentParser:
    """One cockpit action: a one-line summary for the list, a description and examples for its own help."""
    return actions.add_parser(
        name,
        help=summary,
        description=description,
        epilog="examples:\n" + examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def add_cockpit_launcher_parsers(subparsers):
    """Register the ``cockpit`` verb group on the top-level subparsers."""
    cockpit_root = subparsers.add_parser(
        "cockpit",
        help="Bring up all your practices in one tmux layout and keep it alive",
        description=_ROOT_DESCRIPTION,
        epilog=_ROOT_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    actions = cockpit_root.add_subparsers(
        dest="cockpit_action",
        metavar="action",
    )

    # launch
    launch = _action(
        actions,
        "launch",
        "Bring up the cockpit (attaches if it is already running)",
        "Build the layout the config describes and attach to it. Safe to run again: a cockpit that is already\n"
        "running is attached, not rebuilt, and a practice pane that was closed is added back. Conversations are\n"
        "not restored by launch; `refresh` resumes a pane whose program exited.\n\n"
        "Each claude pane this launch opened gets one wake turn in the background, once it is idle, so it reads its\n"
        "inbox and arms its listener without anyone typing. A pane that is busy or has a draft is not typed into.\n"
        "`empirica cockpit status` shows how many were woken.\n\n"
        "With the tmux surface the layout opens in the terminal you run this from, so it works over SSH and on a\n"
        "machine with no display. alacritty and ghostty open a window on this machine's display.",
        "  empirica cockpit launch\n"
        "  empirica cockpit launch --profile monitor-b\n"
        "  empirica cockpit launch --profile server --surface tmux   over SSH\n"
        "  empirica cockpit launch --no-attach                        build it, stay in this shell\n",
    )
    launch.add_argument("--config", metavar="PATH", help=_CONFIG_HELP)
    launch.add_argument("--profile", metavar="NAME", help=_PROFILE_HELP)
    launch.add_argument(
        "--no-attach",
        action="store_true",
        help="Build the layout and do not attach: for scripted bring-up",
    )
    launch.add_argument(
        "--quiet-warnings",
        action="store_true",
        help="Do not print the warning that the previous cockpit ended without `kill` or `detach`",
    )
    launch.add_argument(
        "--surface",
        choices=["tmux", "alacritty", "ghostty"],
        help="Override `surface:` from the config for this launch. tmux: the layout opens in this terminal "
        "(works over SSH, needs no display). alacritty, ghostty: one new window on this machine's display, with "
        "a window class per session so a taskbar slot can be pinned to it; ghostty also takes drag-and-drop "
        "file paste",
    )
    launch.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # refresh
    refresh = _action(
        actions,
        "refresh",
        "Bring back, in place, every practice pane whose claude, codex or ecodex exited",
        "Restart every practice pane whose program exited: same pane, same directory, same EMPIRICA_INSTANCE_ID,\n"
        "conversation resumed (`claude --continue`, or the project's `resume:` command), then wake each resumed\n"
        "claude once it is idle. A running practice is never touched, so this is safe to run at any time. A pane\n"
        "that was closed entirely is listed as missing; `empirica cockpit launch` adds it back.",
        "  empirica cockpit refresh\n"
        "  empirica cockpit refresh --profile monitor-b\n"
        "  empirica cockpit refresh --pane %7                only that pane\n",
    )
    refresh.add_argument("--config", metavar="PATH", help=_CONFIG_HELP)
    refresh.add_argument("--profile", metavar="NAME", help=_PROFILE_HELP)
    refresh.add_argument(
        "--pane",
        metavar="ID",
        help="Restart only this pane (a tmux pane id such as %%7), if its program exited. Other dead panes are left "
        "as they are and listed. The cockpit bar's R chip runs this for the pane you are in",
    )
    refresh.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # status
    status = _action(
        actions,
        "status",
        "Show the cockpit's state and check its config; changes nothing",
        "Show what is running and check the config. It touches nothing, so it is also the check for a profile you\n"
        "edited by hand: it exits 1 and names the problem when a pane refers to a project that is not under\n"
        "`projects:`, when a group has no usable pane, when two groups or projects share a name, or when the file\n"
        "cannot be read. A project directory that does not exist yet is a warning, not an error. It also shows what\n"
        "the last launch did about waking the panes it opened: how many, which were not woken and why.",
        "  empirica cockpit status\n"
        "  empirica cockpit status --profile monitor-b\n"
        "  empirica cockpit status --profile monitor-b --output json\n",
    )
    status.add_argument("--config", metavar="PATH", help=_CONFIG_HELP)
    status.add_argument("--profile", metavar="NAME", help=_PROFILE_HELP)
    status.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # detach
    detach = _action(
        actions,
        "detach",
        "Detach from the cockpit and record a clean shutdown; practices keep running",
        "Detach this terminal from the cockpit and record a clean shutdown. The tmux session and every practice\n"
        "in it keep running; `empirica cockpit launch` attaches again.",
        "  empirica cockpit detach\n",
    )
    detach.add_argument("--output", choices=["human", "json"], default="human", help="Output format")

    # kill
    kill = _action(
        actions,
        "kill",
        "End the cockpit session and record a clean shutdown",
        "End the cockpit's tmux session, which stops every practice in it, and record a clean shutdown so the next\n"
        "launch does not warn. Everything the practices logged is kept.",
        "  empirica cockpit kill\n  empirica cockpit kill --profile monitor-b\n  empirica cockpit kill --prune\n",
    )
    kill.add_argument("--config", metavar="PATH", help=_CONFIG_HELP)
    kill.add_argument("--profile", metavar="NAME", help=_PROFILE_HELP)
    kill.add_argument(
        "--prune",
        action="store_true",
        help="Also remove the state files of instances that are gone (the same as `empirica instance prune`)",
    )
    kill.add_argument("--output", choices=["human", "json"], default="human", help="Output format")
