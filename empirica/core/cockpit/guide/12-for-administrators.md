# 12. For administrators: cockpit settings and practice setup

This page is for whoever sets up cockpits and practices for others.

## Cockpit settings

A cockpit is described by a settings file in `~/.empirica/cockpit/`: `config.yaml` for the
main cockpit, `config-NAME.yaml` for each extra one (opened with `--profile NAME`). It lists
the practices, how they group into tabs, and which program runs in each box.

| To | Run |
|---|---|
| see what a cockpit would open, without starting it | `empirica cockpit status [--profile NAME]` |
| start it, or attach to it if it is running | `empirica cockpit launch [--profile NAME]` |
| restart stopped boxes from outside the cockpit | `empirica cockpit refresh [--profile NAME]` |

After editing the file, run `launch` again: added tabs appear, and nothing running is
restarted.

## Practice setup

A practice is a folder with a `.empirica/` directory whose `project.yaml` carries its fixed
identity (a project id and an ai_id). Moving a practice to another machine means copying
or cloning that folder with `project.yaml` intact and registering it there
(`empirica project-register .`); never create a fresh identity for an existing practice.
A new practice is created with `empirica provision-practice NAME` (page 11); its `--help`
lists the options.
