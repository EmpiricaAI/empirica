"""Which tmux panes belong to a practice, as the `tmux_<pane>` ids `wake_instance` accepts (goal 87772ac4).

The listener service for a practice knows its `ai_id`; `wake_instance` knows panes. A practice can run several seats at once, and a
pane is one of two kinds: a NAMED seat (its claude declares `EMPIRICA_INSTANCE_ID`, found by walking the process tree up to the pane
shell) or an unbound `tmux_<pane>` session that never declared one. Both are resolved to a practice the way the cockpit does, from
the project the instance is bound to, so a wake reaches exactly the panes the cockpit shows under that practice.

Every source is injectable; the defaults read the live box and fail empty.
"""

from __future__ import annotations

from collections.abc import Callable


def _ai_of_instance(instance_id: str) -> str | None:
    from empirica.core.cockpit.instance_state import _instance_project_path, _project_ai_id

    return _project_ai_id(_instance_project_path(instance_id))


def _live_seat_panes() -> dict[str, str]:
    from empirica.core.cockpit.liveness import claude_seat_panes

    return claude_seat_panes()


def _live_unbound_panes() -> list[str]:
    from empirica.core.cockpit.liveness import _live_tmux_panes, claude_pane_bindings

    live = _live_tmux_panes() or set()
    owned = claude_pane_bindings()[1]
    return sorted(live - owned)


def wakeable_panes(
    ai_id: str,
    *,
    seat_panes: Callable[[], dict[str, str]] = _live_seat_panes,
    unbound_panes: Callable[[], list[str]] = _live_unbound_panes,
    ai_of: Callable[[str], str | None] = _ai_of_instance,
) -> list[str]:
    """`tmux_<pane>` ids of the live Claude panes bound to practice `ai_id`, sorted, no duplicates. Empty when nothing can be found."""
    found: set[str] = set()
    try:
        for instance_id, pane in seat_panes().items():
            # A named seat's own name is its practice when no project binding says otherwise.
            if (ai_of(instance_id) or instance_id) == ai_id:
                found.add(f"tmux_{pane}")
        for pane in unbound_panes():
            if ai_of(f"tmux_{pane}") == ai_id:
                found.add(f"tmux_{pane}")
    except Exception:
        return sorted(found)
    return sorted(found)
