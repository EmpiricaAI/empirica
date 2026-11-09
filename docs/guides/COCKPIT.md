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

The cockpit is for people who run Claude Code or ecodex in a terminal. You need `tmux` 3.0 or newer. For the one-window-per-monitor layouts below you
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

## The bars: practices on top, commands under each pane

A cockpit shows your practices at the top and its commands on the line under each pane, so
routine actions do not need a command line.

```
 1 core   2 > cortex   3 ✗ outreach   4 ! research          serve ● cortex ● load 2.1  14:02
 ...
── waiting: approve Bash  ☰ commands  R restart  A refresh 1  M listener  I inbox 2  C claude  S sessions  L login  ? help ──
```

The top line is your practices, with whatever your tmux `status-right` already shows on the
right. A red ✗ on a practice means a pane in it has stopped; an orange `!` means a pane in it is
waiting on you. When the practices do not fit, the right-hand readout moves to a second line at the top.

**Waiting on you.** When Claude Code in a pane shows a permission dialog or asks a question, the
line under that pane starts with `waiting: approve <tool>` or `waiting: question`, and its practice
tab gets the `!`. The mark clears on the pane's next Edit, Write or Bash, a failed tool, a prompt or the
turn ending, because Claude Code has no event for a dialog being answered; a turn that goes on
with only reads or MCP calls keeps it until then. If a dialog goes
unanswered for a few seconds, Claude Code says so and the cockpit sends a desktop notification
(through `notify-send`, where it is installed). Sessions outside tmux are never notified;
any tmux pane gets the mark, a cockpit's bar is where it shows.

The line under each pane shows the commands, coloured by state: red is broken now, orange is
waiting, dim is nothing to do. `R` is about the pane it sits under.

**Click anywhere on that line** to open the command menu. Every item is clickable, and the menu
closes on its Close item, on Escape, or on a click outside it. From a keyboard: `Ctrl+b`, `e`,
then the letter, or `Ctrl+b`, `e`, `Space` for the menu.

| Command | What it does | Red when | Orange when |
|---|---|---|---|
| `R` | Restart this pane: same practice, same identity, conversation resumed, then woken | this pane has stopped | |
| `A` | Restart every pane that has stopped | any pane has stopped | |
| `M` | Wake this practice so it reads its mail and re-arms its listener | its listener service is down | more than one listener runs for it (every message arrives twice) |
| `I` | Show this practice's unanswered mail | | mail is waiting |
| `C` | Update the AI client (asks first) | | a newer release exists |
| `S` | Restart every practice pane onto new settings (asks first; it ends them) | | settings changed since they started |
| `L` | Renew the Cortex login, in a window of its own | the login has expired | it expires soon |
| `?` | Help: the legend, the commands and a short guide to working with practices | | |

The menu also has **update Empirica on this machine**, near its end. It has no chip and no key,
so nobody starts an update of the whole machine by accident, and it asks first like `C` and `S`.
In what follows it is called `U`.

`U`, `C` and `S` ask first with a menu: Yes and Cancel are both clickable. The help and the inbox
open in a viewer with a Close button and scroll buttons at the top; the wheel, the arrow keys and
`q` work too. Login and the machine commands run in a window of their own, so you can read what
they print; the window closes ten seconds after the command finishes.

What `U`, `C` and `S` run, in order of precedence:

1. the `fix` command for that component in `~/.empirica/ecosystem-state.json`, when the machine
   has one (written by whatever keeps the machine up to date);
2. `bar_commands` in the cockpit config:

   ```yaml
   bar_commands:
     U: pipx upgrade empirica && empirica setup-claude-code --force
     C: claude update
   ```

3. a built-in: `claude update` for `C`, and for `S` a restart of every practice pane with its
   conversation resumed. `U` has no built-in, because the right update command depends on how
   Empirica was installed; pressing it says which key to set.

Where the colours come from:

- `R` and `A` are computed by tmux from stopped panes, so they are always current.
- `M` and `I` are refreshed in the background: at launch, after each command, and at most every
  five minutes. The status line itself never runs a check.
- `U`, `C`, `S` and `L` read `~/.empirica/ecosystem-state.json` when the machine has one. The
  file names, per component, a verdict (`needed`, `recommended` or `ok`), a short label and the
  command that fixes it. Without the file these stay dim.

`bar: false` in a cockpit config turns the bars off for that cockpit. They are set on the
cockpit's own tmux session, so your other tmux sessions look as they did. Key bindings are shared
by a whole tmux server, so a `Ctrl+b e` you already use is not taken (the commands then open from
a click only, and `launch` says so), and a click on a pane border outside a cockpit goes to
whatever handled it before.

The guide the help shows is a set of markdown pages in `empirica/core/cockpit/guide/`, one per topic,
written for people who do not write code. Edit them there.

`empirica cockpit refresh --pane ID` is what `R` runs: it restarts that one pane if it has
stopped and leaves every other pane alone.

---

## A cockpit on another machine, over SSH

A cockpit is a tmux session, so it runs wherever tmux does and survives your
connection. On the remote machine, use the `tmux` surface (the others open a
window on that machine's display, which an SSH session does not have):

```yaml
# ~/.empirica/cockpit/config-server.yaml on the remote machine
session_name: cockpit-server
surface: tmux
attach_on_launch: true
```

```bash
ssh server
empirica cockpit status --profile server    # checks the profile, changes nothing
empirica cockpit launch --profile server    # the layout opens in your SSH terminal
```

Closing the terminal or losing the connection detaches you; the practices keep
running. `ssh server` and `empirica cockpit launch --profile server` attach again.
`empirica cockpit detach` does the same on purpose and records a clean shutdown.

If you run a command without a login shell (`ssh server 'empirica cockpit status'`),
`empirica`, `claude` and `tmux` must be on the PATH that kind of session sees,
which is often shorter than your interactive one.

---

## Two harnesses in one practice

A practice is a directory. Two practitioners can work in it side by side, for
example Claude Code and ecodex: they share the practice's knowledge, goals and
project file, and each has its own instance id, so their transactions do not mix.
The Claude Code pane takes the project `name` as its id; ecodex generates its own.

```yaml
projects:
  - name: research
    path: ~/code/research
    launch: claude
  - name: research-ecodex
    path: ~/code/research            # the same directory
    launch: ecodex

groups:
  - name: research
    split: horizontal
    panes:
      - {project: research}
      - {project: research-ecodex}
```

`refresh` brings each back with its own conversation: the claude pane's own session
by id (see [When a practice dies](#when-a-practice-dies-refresh)), and `ecodex resume --last`
for the second. `provision-practice research
--cockpit-profile P --second-pane ecodex` writes both entries and the window for you
(the second one is named `research-ecodex`). `--launch ecodex` alone makes a practice
whose only pane runs ecodex.
Set `instance_id:` on the ecodex project only if you want a fixed, readable id in
place of the one it generates.

---

## The config, key by key

```yaml
session_name: cockpit          # tmux session name; unique per profile
attach_on_launch: true         # tmux surface: attach after building
surface: ghostty               # tmux | alacritty | ghostty
mouse: true                    # clickable tabs and panes
bar: true                      # the bars (see above); false turns them off
bar_commands: {U: '...'}       # optional: what U, C and S run on this machine

projects:                      # everything a pane may reference
  - name: empirica             # also the default identity (see below)
    path: ~/empirical-ai/empirica
    launch: claude --dangerously-skip-permissions
    instance_id: core          # optional; "" = never bind an identity
    resume: claude --continue  # optional; replaces refresh's own choice (the pane's session by id, else --continue)

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

A cockpit profile says where practices appear. What a practice *is* (its project type and domain) is set in the
practice's own `.empirica/project.yaml`: see [What a project is and what work is being done](../reference/TAXONOMY.md#46-what-a-project-is-and-what-work-is-being-done).

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
identity, and resumes the conversation that pane was in: `claude --resume <id>`,
with the session id the pane's own record holds. The id is used only when that
record is for the practice's directory and a transcript with that id was started
there; otherwise the pane resumes the newest conversation in its directory
(`claude --continue`). A `--continue` or a bare `--resume` in your `launch:` is
replaced accordingly; `resume:` in the config overrides all of this. The refresh
result names the session each pane resumed. A running practice is never touched,
so `refresh` is safe to run at any time.

If a practice pane was closed entirely, `refresh` lists it as missing and
`empirica cockpit launch` adds back exactly that pane.

### After a restart: `launch` wakes what it opened

A claude that has just started, or resumed with `--continue`, sits at its prompt
until someone types. Its session-start instructions (arm the mesh listener, read
the inbox) run on its first turn, so a cockpit brought up after a reboot used to
be a row of idle practices that looked ready and were not listening.

`launch` now gives each claude pane it opened one wake turn, the same one
`refresh` gives a pane it restarts. It happens in the background, once the pane
is idle, so `launch` is as fast as before; a pane that is busy, has a draft in
its prompt or shows a dialog is never typed into. Practices that were already
running are not touched.

```bash
empirica cockpit status        # "launch wake: 6 of 6 claude pane(s) woken at ..."
```

`status` names any pane that was not woken and why, and says so if the wake
failed or never finished.

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
| The wrong conversation resumed after `refresh` | `refresh` resumes the pane's own session by id when it can confirm it, else the newest conversation in that directory (`--continue`). Check the session it names in its result. A `resume:` line in the config replaces both; remove it to get the by-id resume. |
