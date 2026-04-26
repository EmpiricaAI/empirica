"""Empirica Cockpit TUI — interactive Textual app.

Bound to the `empirica tui` CLI command. Reads the same JSON as the rest
of the cockpit (`empirica.core.cockpit.aggregate_all()`) and exposes
clickable + keyboard-driven controls for every state-changing verb.
"""

from empirica.cli.tui.cockpit_app import CockpitApp, run_tui

__all__ = ['CockpitApp', 'run_tui']
