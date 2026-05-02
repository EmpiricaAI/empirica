"""StatuslinePanel widget — empirica chat header strip showing epistemic state.

Per CHAT.md spec: condensed 1-line panel below the Header showing
phase + key vectors + open goals/unknowns counts. /statusline command
cycles modes (basic | default | learning | full) lifting the renderer
modes from CC plugin's statusline_empirica.py.

Reuses existing empirica primitives — no duplication:
  - empirica.utils.session_resolver.get_instance_id  (current instance)
  - empirica.core.cockpit.enrichment.statusline_summary  (live vectors)
  - empirica.core.signaling.format_vectors_compact      (rendering)

Refreshes on a 2s tick to match cockpit_app's REFRESH_SECONDS.

When no live transaction state is available (e.g., chat launched
without prior empirica activity in the project), renders a muted
placeholder rather than going blank.
"""

from __future__ import annotations

from textual.widgets import Static

# Order matters — cycling /statusline goes through these in sequence.
RENDER_MODES = ("basic", "default", "learning", "full")


class StatuslinePanel(Static):
    """One-line statusline strip rendered just below the Header."""

    DEFAULT_CSS = """
    StatuslinePanel {
        height: 1;
        padding: 0 1;
        background: $boost;
        color: $primary;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__("(statusline loading…)", id="chat-statusline", **kwargs)
        self._mode: str = "default"

    def cycle_mode(self) -> str:
        """Advance to the next render mode; returns the new mode name."""
        idx = RENDER_MODES.index(self._mode) if self._mode in RENDER_MODES else 0
        self._mode = RENDER_MODES[(idx + 1) % len(RENDER_MODES)]
        self.refresh_now()
        return self._mode

    def set_mode(self, mode: str) -> bool:
        if mode not in RENDER_MODES:
            return False
        self._mode = mode
        self.refresh_now()
        return True

    def current_mode(self) -> str:
        return self._mode

    def refresh_now(self) -> None:
        """Pull a fresh statusline summary and update the widget body."""
        body = self._build_text()
        self.update(body)

    def _build_text(self) -> str:
        """Build the line based on current mode."""
        try:
            from empirica.core.cockpit.enrichment import statusline_summary
            from empirica.utils.session_resolver import (
                InstanceResolver,
                get_instance_id,
            )
        except Exception as e:  # noqa: BLE001 — surface if empirica internals shift
            return f"[dim]statusline unavailable: {type(e).__name__}[/dim]"

        instance_id = get_instance_id()
        if not instance_id:
            return "[dim]no instance_id (chat not bound to empirica session)[/dim]"

        project_path = None
        session_id = None
        try:
            resolver = InstanceResolver()
            project_path = str(resolver.project_path()) if hasattr(resolver, "project_path") else None
            # Try to resolve current session — may not exist if chat hasn't
            # opened a transaction yet (Phase 6 v1 doesn't auto-PREFLIGHT).
            if hasattr(resolver, "session_id"):
                try:
                    session_id = resolver.session_id()
                except Exception:  # noqa: BLE001
                    session_id = None
        except Exception:  # noqa: BLE001
            pass

        try:
            summary = statusline_summary(
                instance_id=instance_id,
                label_fallback=None,
                project_path=project_path,
                session_id=session_id,
            )
        except Exception as e:  # noqa: BLE001
            return f"[dim]statusline error: {type(e).__name__}[/dim]"

        return self._format_summary(summary)

    def _format_summary(self, summary) -> str:
        """Render based on _mode."""
        if not getattr(summary, "found", False):
            # No live state — render minimal placeholder
            return "[dim]· no active transaction · use /preflight to start tracking[/dim]"

        if self._mode == "basic":
            conf = summary.confidence
            return f"conf {conf:.0%}" if conf is not None else "·"

        # Build a vectors dict for format_vectors_compact
        from empirica.core.signaling import format_vectors_compact
        vectors_dict: dict[str, float] = {}
        for k in ("know", "uncertainty", "context", "clarity", "completion"):
            v = getattr(summary, k, None)
            if v is not None:
                vectors_dict[k] = v

        if self._mode == "default":
            keys = ["know", "uncertainty", "context"]
            v_part = format_vectors_compact(vectors_dict, keys=keys, use_percentage=True)
            counts = ""
            if summary.open_goals is not None:
                counts = f"  ·  goals {summary.open_goals}"
            return f"{v_part}{counts}"

        if self._mode == "learning":
            keys = ["know", "uncertainty", "context", "clarity"]
            v_part = format_vectors_compact(vectors_dict, keys=keys, use_percentage=True)
            return f"{v_part}"

        # full mode
        keys = ["know", "uncertainty", "context", "clarity", "completion"]
        v_part = format_vectors_compact(vectors_dict, keys=keys, use_percentage=True)
        extras = []
        if summary.open_goals is not None:
            extras.append(f"goals {summary.open_goals}")
        if summary.artifact_count is not None:
            extras.append(f"artifacts {summary.artifact_count}")
        if summary.confidence is not None:
            extras.append(f"conf {summary.confidence:.0%}")
        suffix = "  ·  " + "  ·  ".join(extras) if extras else ""
        return f"{v_part}{suffix}"
