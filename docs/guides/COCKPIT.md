# The Cockpit: one command for all your practices

If you work with more than one practice (one Claude per project), the cockpit
brings them all up at once. It opens a tmux layout with one pane per practice,
each started in its own project directory and bound to its own identity, and
keeps that layout recoverable when a pane dies or the machine reboots.

```bash
empirica cockpit launch                   # bring it up (or re-attach)
empirica cockpit refresh                  # restart any practice whose claude exited
empirica cockpit status                   # what's running, without attaching
empirica cockpit kill                     # tear it down cleanly
```

You need `tmux` 3.0 or newer. For the one-window-per-monitor layouts below you
also need `alacritty` or `ghostty`.

For how Empirica keeps panes isolated from each other (separate transactions,
goals and sessions per pane), see [TMUX_MULTI_PANE_GUIDE.md](TMUX_MULTI_PANE_GUIDE.md).
This guide is about bringing the layout up and keeping it healthy.

---

## Your first cockpit

The first `empirica cockpit launch` writes `~/.empirica/cockpit/config.yaml`
if it does not exist. The generated config has one window per project it finds
under `~/empirical-ai/` and `~/empirica/` (any folder with a `.empirica/` inside; the
second is where `provision-practice` puts a practice by default) and a status
window. It then opens the layout in your terminal.

To choose the practices yourself, write the config first. Here are two practices
side by side in one terminal window:

```yaml
# ~/.empirica/cockpit/config.yaml
session_name: cockpit
surface: tmux            # the layout opens in the terminal you launched from

projects:
  - name: my-api
    path: ~/code/my-api
    launch: claude
  - name: my-docs
    path: ~/code/my-docs
    launch: claude

groups:
  - name: work
    split: horizontal     # left | right
    panes:
      - {project: my-api}
      - {project: my-docs}
```

```bash
empirica cockpit launch
```

Each pane starts `claude` in its project directory. Click a pane to focus it.
When there are several groups, each is a tab in the status line; click a tab to
switch (mouse support is on by default).

---

## From onboarding

If you ran `/ewm-interview` and let it provision your practices, it already wrote a
cockpit for them: each `empirica provision-practice <name> --cockpit-profile <profile>`
adds one practice to `~/.empirica/cockpit/config-<profile>.yaml` (creating it with a
TUI window on the first call). The interview ends by printing the command:

```bash
empirica cockpit launch --profile <profile>
```

You can run the same flag yourself to add a practice later. It is safe to repeat: a
practice already listed changes nothing. A profile you edited is backed up to
`config-<profile>.yaml.bak` before it is rewritten (rewriting drops YAML comments), and
one that no longer parses is refused rather than replaced.

---

## Several cockpits: profiles

A second layout, say a different set of practices or one per monitor, is a
second config file named `config-NAME.yaml` beside the first:

```bash
empirica cockpit launch --profile monitor-b     # reads ~/.empirica/cockpit/config-monitor-b.yaml
empirica cockpit status --profile monitor-b
empirica cockpit refresh --profile monitor-b
empirica cockpit kill   --profile monitor-b
```

`cockpit status` is also the check for a profile you edited by hand, and it touches nothing.
It exits 1 and lists the problem when a pane names a project that is not under `projects:`,
when a group has no usable pane, when two groups or projects share a name, or when the file
cannot be read (otherwise you would be shown the built-in layout as if it were yours). A
project directory that does not exist yet is a warning: launch leaves a placeholder pane.

Give each profile its own `session_name`, since each is its own tmux session. An
unknown profile name is refused and the error lists the profiles that exist. A
typo never brings up the wrong cockpit or writes a new default.

`--config PATH` points at any config file instead of a profile.

---

## Surfaces: where the layout appears

| `surface:` | What you get |
|---|---|
| `tmux` | The layout opens in the terminal you ran `launch` from. Needs `projects` (and optionally `groups`). |
| `alacritty` | One new alacritty window for the whole config, with window class `empirica-<session>`. |
| `ghostty` | One new ghostty window for the whole config, with app id `com.empirica.cockpit.<session>`. Supports drag-and-drop file paste. |

With `alacritty` or `ghostty`, pin each cockpit's window to a taskbar slot and
the per-session class lets you jump to it (Meta+1..N on KDE). Re-running `launch`
while the window is still open does not spawn a duplicate. If the window was
closed but the tmux session survived (a crash, a hibernate), `launch` wraps the
surviving session in a new window and your running practices are untouched.

`--surface` overrides the config for one launch. Extra terminal arguments go in
`alacritty_args:` (used for ghostty too).

---

## The config, key by key

```yaml
session_name: cockpit          # tmux session name; unique per profile
attach_on_launch: true         # tmux surface: attach after building
surface: ghostty               # tmux | alacritty | ghostty
mouse: true                    # clickable tabs and panes

projects:                      # everything a pane may reference
  - name: empirica             # also the default identity (see below)
    path: ~/empirical-ai/empirica
    launch: claude --dangerously-skip-permissions
    instance_id: core          # optional; "" = never bind an identity
    resume: claude --continue  # optional; what `refresh` runs to bring it back

groups:                        # one tmux window per group
  - name: core
    split: horizontal          # horizontal = side by side, vertical = stacked
    panes:
      - {project: empirica}                           # a practice
      - {command: "empirica tui", label: tui}         # any command
      - {command: bash, label: spare}                 # a spare shell

on_abnormal_exit:
  warn: true                   # say so when the last cockpit ended without `kill`/`detach`
```

Without `groups:`, each project becomes its own window in one session and
`status_windows:` (`{name, command}` entries) add windows such as a monitor.

A project whose `path` does not exist yet gets a placeholder pane that names the
missing directory. So a layout can list practices before they are provisioned,
and claude is never started in the wrong place.

---

## Identity: why a practice survives a restart

Empirica keys a practitioner's transactions, goals and calibration by an
instance id. Without the cockpit that id comes from the tmux pane (`tmux_4`),
which changes whenever the pane is recreated. So a restarted practice would
come back as a stranger.

The cockpit gives every project pane a stable id instead: the project `name`,
or `instance_id:` if you set one. It is passed to the pane as
`EMPIRICA_INSTANCE_ID` and recorded on the pane as the tmux option
`@empirica_instance_id`. This applies when `launch` is `claude` or a shell
(`bash`, `zsh`...). Other programs are left alone unless you set `instance_id:`
explicitly, because some (ecodex, for one) set their own id. If two panes would
share an id, the second gets a suffix, since sharing one would mix their
transaction files.

---

## When a practice dies: `refresh`

If claude exits in a practice pane (a crash, `/exit`, an update that needed a
restart), the pane stays where it was, marked dead, instead of vanishing from
the layout.

```bash
empirica cockpit refresh
```

`refresh` restarts every dead practice pane in place, in its directory, with its
identity, and resumes its last conversation (`claude --continue`). If your
`launch:` uses a bare `--resume`, which would stop at a session picker, `refresh`
swaps it for `--continue`; `resume:` in the config overrides this. A running
practice is never touched, so `refresh` is safe to run at any time.

If a practice pane was closed entirely, `refresh` lists it as missing and
`empirica cockpit launch` adds back exactly that pane.

Cockpits started by an older version are picked up on the first `refresh`. A
pane is matched to its configured practice when its window and directory
identify exactly one. Anything ambiguous is reported and left alone.

---

## Shutting down, and crashes

```bash
empirica cockpit detach        # detach and record a clean shutdown; practices keep running
empirica cockpit kill          # end the session and record a clean shutdown
empirica cockpit kill --prune  # also remove leftover state for instances that are gone
```

If the cockpit ends any other way (reboot, OOM, a killed terminal), the next
`launch` warns you and suggests `empirica instance prune --dry-run` to see what
dead instances left behind.

`empirica doctor` reports transaction files left open for more than a week,
which is what an abandoned pane leaves behind, together with how to close one.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `tmux binary not found on PATH` | Install tmux 3.0+. |
| `ghostty not found on PATH` | Install it, or `--surface alacritty` / `surface: tmux`. |
| A practice shows as `tmux_N` in `empirica status --all` | It was started outside the cockpit, or by a cockpit older than identity binding. `refresh` binds it the next time it restarts. |
| `refresh` says a pane "could not be matched" | Its window or directory differs from the config, or two practices share a directory. Recreate the layout with `kill` + `launch` when convenient. |
| The wrong practice resumed after `refresh` | `--continue` resumes the most recent conversation *in that directory*. Two practices sharing a directory share that history; give each its own path, or set `resume:` to `claude --resume <session-id>`. |
