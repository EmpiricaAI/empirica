#!/usr/bin/env python3
"""
System Dashboard - Unified observability for the Noetic OS kernel.

Like /proc or htop for AI cognition: aggregates status from all kernel
subsystems into a single queryable surface.

Subsystems monitored:
  MCOLoader            - Config state (model, persona, loaded configs)
  ContextBudgetManager - Memory utilization (zones, pressure, evictions)
  EpistemicBus         - Event throughput (observer count, event count)
  BusPersistence       - Storage backends (SQLite, Qdrant availability)
  AttentionBudget      - Investigation resource allocation
  Sentinel             - Access control (gate status, phase)

Architecture: EpistemicObserver on the bus (push) + lazy queries (pull).
- Push: CBM events (pressure, eviction, injection, page fault) update cached state
- Pull: MCOLoader, Sentinel queried on get_system_status()

Usage:
    from empirica.core.system_dashboard import get_dashboard, SystemStatus

    dashboard = get_dashboard(session_id="abc123")
    status = dashboard.get_system_status()

    # Quick dict for CLI/MCP
    status_dict = status.to_dict()
"""

import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from empirica.core.epistemic_bus import (
    EpistemicEvent,
    EpistemicObserver,
    EventTypes,
    get_global_bus,
)

logger = logging.getLogger(__name__)


# --- Status Data Structures ---


@dataclass
class ConfigStatus:
    """MCOLoader state snapshot."""

    model: str = "unknown"
    persona: str = "unknown"
    cascade_style: str = "default"
    loaded_configs: list[str] = field(default_factory=list)
    available_configs: list[str] = field(default_factory=list)
    load_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryStatus:
    """ContextBudgetManager state snapshot."""

    node_id: str = "unknown"
    tokens_used: int = 0
    tokens_available: int = 0
    utilization_pct: float = 0.0
    item_count: int = 0
    anchor_tokens: int = 0
    working_tokens: int = 0
    cache_tokens: int = 0
    page_faults: int = 0
    evictions: int = 0
    under_pressure: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BusStatus:
    """EpistemicBus + BusPersistence state snapshot."""

    observer_count: int = 0
    event_count: int = 0
    sqlite_active: bool = False
    qdrant_active: bool = False
    sqlite_events: int = 0
    qdrant_events: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AttentionStatus:
    """AttentionBudgetCalculator state snapshot."""

    has_budget: bool = False
    total_budget: int = 0
    remaining: int = 0
    utilization: float = 0.0
    domains: int = 0
    strategy: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IntegrityStatus:
    """Placeholder for removed MemoryGapDetector — always returns healthy."""

    gaps_detected: bool = False
    gap_count: int = 0
    overall_gap: float = 0.0
    severity_counts: dict[str, int] = field(default_factory=dict)
    confabulation_risk: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GateStatus:
    """Sentinel gate state snapshot."""

    phase: str = "unknown"  # preflight, check, postflight, or loop_closed
    decision: str = "unknown"  # proceed, investigate, or escalate
    know: float = 0.0
    uncertainty: float = 1.0
    gate_passed: bool = False
    transaction_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NodeIdentity:
    """Node metadata for multi-agent context."""

    ai_id: str = "unknown"
    session_id: str = "unknown"
    uptime_seconds: float = 0.0
    started_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SystemStatus:
    """Complete system status - the unified /proc snapshot."""

    timestamp: float = 0.0
    node: NodeIdentity = field(default_factory=NodeIdentity)
    config: ConfigStatus = field(default_factory=ConfigStatus)
    memory: MemoryStatus = field(default_factory=MemoryStatus)
    bus: BusStatus = field(default_factory=BusStatus)
    attention: AttentionStatus = field(default_factory=AttentionStatus)
    integrity: IntegrityStatus = field(default_factory=IntegrityStatus)
    gate: GateStatus = field(default_factory=GateStatus)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "node": self.node.to_dict(),
            "config": self.config.to_dict(),
            "memory": self.memory.to_dict(),
            "bus": self.bus.to_dict(),
            "attention": self.attention.to_dict(),
            "integrity": self.integrity.to_dict(),
            "gate": self.gate.to_dict(),
        }

    def format_summary(self) -> str:
        """One-line summary for statusline or quick display."""
        mem = self.memory
        gate = self.gate
        bus = self.bus
        pressure = "!" if mem.under_pressure else ""
        return (
            f"[{self.node.ai_id}] "
            f"mem:{mem.utilization_pct:.0f}%{pressure} "
            f"({mem.item_count} items) "
            f"bus:{bus.event_count}ev/{bus.observer_count}obs "
            f"gate:{gate.phase}/{gate.decision} "
            f"know={gate.know:.2f} unc={gate.uncertainty:.2f}"
        )

    def format_display(self) -> str:
        """Multi-line formatted display for CLI."""
        n = self.node
        c = self.config
        m = self.memory
        b = self.bus
        a = self.attention
        i = self.integrity
        g = self.gate

        lines = [
            "╔═══════════════════════════════════════════════════════════╗",
            "║  NOETIC OS - System Status                               ║",
            "╚═══════════════════════════════════════════════════════════╝",
            "",
            f"  Node:     {n.ai_id} (session: {n.session_id[:8]}...)",
            f"  Uptime:   {n.uptime_seconds:.0f}s",
            "",
            "  ── Config ──────────────────────────────────────────────",
            f"  Model:    {c.model}  |  Persona: {c.persona}",
            f"  Configs:  {c.load_count}/{len(c.available_configs)} loaded"
            f"  ({', '.join(c.loaded_configs) if c.loaded_configs else 'none'})",
            "",
            "  ── Memory ──────────────────────────────────────────────",
            f"  Usage:    {m.tokens_used:,}t / {m.tokens_used + m.tokens_available:,}t"
            f"  ({m.utilization_pct:.1f}%)"
            f"{'  ⚠ PRESSURE' if m.under_pressure else ''}",
            f"  Zones:    anchor={m.anchor_tokens:,}t  working={m.working_tokens:,}t  cache={m.cache_tokens:,}t",
            f"  Items:    {m.item_count}  |  Page faults: {m.page_faults}  |  Evictions: {m.evictions}",
            "",
            "  ── Bus ─────────────────────────────────────────────────",
            f"  Events:   {b.event_count} published  |  Observers: {b.observer_count}",
            f"  Storage:  SQLite={'on' if b.sqlite_active else 'off'}"
            f" ({b.sqlite_events} persisted)"
            f"  Qdrant={'on' if b.qdrant_active else 'off'}"
            f" ({b.qdrant_events} persisted)",
            "",
            "  ── Attention ───────────────────────────────────────────",
        ]

        if a.has_budget:
            lines.append(
                f"  Budget:   {a.remaining}/{a.total_budget}"
                f"  ({a.utilization:.0%} used)"
                f"  |  Domains: {a.domains}"
                f"  |  Strategy: {a.strategy}"
            )
        else:
            lines.append("  Budget:   (none allocated)")

        lines += [
            "",
            "  ── Integrity ───────────────────────────────────────────",
        ]
        if i.gaps_detected:
            sev = ", ".join(f"{k}:{v}" for k, v in i.severity_counts.items())
            lines.append(f"  Gaps:     {i.gap_count} detected  (overall: {i.overall_gap:.2f})  [{sev}]")
            if i.confabulation_risk > 0.2:
                lines.append(f"  ⚠ Confabulation risk: {i.confabulation_risk:.2f}")
        else:
            lines.append("  Gaps:     none detected")

        lines += [
            "",
            "  ── Gate ────────────────────────────────────────────────",
            f"  Phase:    {g.phase}  |  Decision: {g.decision}  |  Gate: {'PASSED' if g.gate_passed else 'BLOCKED'}",
            f"  Vectors:  know={g.know:.2f}  uncertainty={g.uncertainty:.2f}",
            "",
        ]

        return "\n".join(lines)


# --- Dashboard Implementation ---


class SystemDashboard(EpistemicObserver):
    """
    Unified kernel observability layer.

    Sits on the EpistemicBus and maintains a cached aggregate view of all
    subsystem states. Push for bus-emitting subsystems (CBM, BusPersistence),
    pull for query-only subsystems (MCOLoader, Sentinel, MemoryGapDetector).
    """

    _instance: Optional["SystemDashboard"] = None

    def __init__(
        self,
        session_id: str,
        node_id: str | None = None,
        auto_subscribe: bool = True,
    ):
        self.session_id = session_id
        self.node_id = node_id or os.getenv("EMPIRICA_AI_ID") or self._resolve_ai_id(session_id)
        self.started_at = time.time()

        # Cached state from bus events (push)
        self._memory_cache: dict[str, Any] = {}
        self._bus_event_count = 0
        self._sqlite_events = 0
        self._qdrant_events = 0
        self._sqlite_active = False
        self._qdrant_active = False

        if auto_subscribe:
            try:
                bus = get_global_bus()
                bus.subscribe(self)
            except Exception as e:
                logger.warning(f"Dashboard could not subscribe to bus: {e}")

    @staticmethod
    def _resolve_ai_id(session_id: str) -> str:
        """Resolve ai_id from the session record in the database."""
        try:
            from empirica.data.session_database import SessionDatabase

            db = SessionDatabase()
            cursor = db.conn.cursor()
            cursor.execute("SELECT ai_id FROM sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            db.close()
            if row and row[0]:
                return row[0]
        except Exception:
            pass
        return "unknown"

    @classmethod
    def get_instance(
        cls,
        session_id: str | None = None,
        node_id: str | None = None,
    ) -> "SystemDashboard":
        if cls._instance is None:
            if session_id is None:
                raise ValueError("session_id required for first dashboard creation")
            cls._instance = cls(session_id=session_id, node_id=node_id)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None

    # --- EpistemicObserver: Push updates from bus ---

    def handle_event(self, event: EpistemicEvent) -> None:
        """Update cached state from bus events."""
        self._bus_event_count += 1

        if event.event_type == EventTypes.MEMORY_PRESSURE:
            self._memory_cache.update(event.data)
            self._memory_cache["under_pressure"] = True

        elif event.event_type == EventTypes.CONTEXT_EVICTED:
            evicted = event.data.get("items_evicted", 0)
            self._memory_cache["evictions"] = self._memory_cache.get("evictions", 0) + evicted

        elif event.event_type == EventTypes.PAGE_FAULT:
            self._memory_cache["page_faults"] = self._memory_cache.get("page_faults", 0) + 1

        elif event.event_type == EventTypes.CONTEXT_INJECTED:
            tokens = event.data.get("tokens", 0)
            self._memory_cache["tokens_used"] = self._memory_cache.get("tokens_used", 0) + tokens

        # Track persistence backend activity from agent_id patterns
        agent = event.agent_id or ""
        if agent.startswith("cbm:"):
            self._memory_cache["node_id"] = event.data.get("node_id", self.node_id)

    # --- Pull queries for non-bus subsystems ---

    def _query_config(self) -> ConfigStatus:
        """Pull MCOLoader state."""
        try:
            from empirica.config.mco_loader import get_mco_config

            mco = get_mco_config()
            return ConfigStatus(
                model=mco.infer_model(self.node_id),
                persona=mco.infer_persona(self.node_id),
                loaded_configs=mco.loaded_configs,
                available_configs=mco.available_configs,
                load_count=mco._load_count,
            )
        except Exception as e:
            logger.debug(f"Config query failed: {e}")
            return ConfigStatus()

    def _query_memory(self) -> MemoryStatus:
        """Pull CBM state (supplements bus-pushed cache)."""
        try:
            from empirica.core.context_budget import get_budget_manager

            mgr = get_budget_manager()
            summary = mgr.get_inventory_summary()
            report = mgr.get_budget_report()
            return MemoryStatus(
                node_id=mgr.node_id,
                tokens_used=summary["tokens_used"],
                tokens_available=summary["tokens_available"],
                utilization_pct=summary["utilization_pct"],
                item_count=summary["item_count"],
                anchor_tokens=summary["zones"]["anchor"],
                working_tokens=summary["zones"]["working"],
                cache_tokens=summary["zones"]["cache"],
                page_faults=summary["page_faults"],
                evictions=summary["evictions"],
                under_pressure=report.under_pressure,
            )
        except Exception:
            # CBM may not be initialized - use bus cache
            return MemoryStatus(
                node_id=self._memory_cache.get("node_id", self.node_id),
                tokens_used=self._memory_cache.get("tokens_used", 0),
                page_faults=self._memory_cache.get("page_faults", 0),
                evictions=self._memory_cache.get("evictions", 0),
                under_pressure=self._memory_cache.get("under_pressure", False),
            )

    def _query_bus(self) -> BusStatus:
        """Pull EpistemicBus + persistence state."""
        try:
            bus = get_global_bus()
            observer_count = bus.get_observer_count()
            event_count = bus.get_event_count()
        except Exception:
            observer_count = 0
            event_count = 0

        # Check persistence backends
        sqlite_active = False
        sqlite_events = 0
        qdrant_active = False
        qdrant_events = 0
        try:
            from empirica.core.bus_persistence import SqliteBusObserver

            sqlite_active = True
            session_events = SqliteBusObserver.query_events(session_id=self.session_id, limit=1000)
            sqlite_events = len(session_events)
        except Exception:
            pass

        try:
            from empirica.core.bus_persistence import QdrantBusObserver

            qobs = QdrantBusObserver(self.session_id)
            qdrant_active = qobs._available
            if qdrant_active:
                qdrant_events = qobs._event_count
        except Exception:
            pass

        return BusStatus(
            observer_count=observer_count,
            event_count=event_count,
            sqlite_active=sqlite_active,
            qdrant_active=qdrant_active,
            sqlite_events=sqlite_events,
            qdrant_events=qdrant_events,
        )

    def _query_attention(self) -> AttentionStatus:
        """Pull AttentionBudgetCalculator state."""
        try:
            from empirica.core.attention_budget import AttentionBudgetCalculator

            calc = AttentionBudgetCalculator(session_id=self.session_id)
            budget = getattr(calc, "_current_budget", None)
            if budget:
                b = budget
                return AttentionStatus(
                    has_budget=True,
                    total_budget=b.total_budget,
                    remaining=b.remaining,
                    utilization=b.utilization,
                    domains=len(b.allocations),
                    strategy=b.strategy,
                )
        except Exception:
            pass
        return AttentionStatus()

    def _query_integrity(self) -> IntegrityStatus:
        """Stub — MemoryGapDetector was removed (overlaps with Qdrant + compact hooks)."""
        return IntegrityStatus()

    def _latest_recorded(self, cursor, column: str, default: float) -> float:
        """The newest non-NULL value of one of this session's vectors, or `default` if it was never recorded."""
        if column not in ("know", "uncertainty"):  # interpolated into SQL: only the two this method serves
            raise ValueError(f"unsupported vector column {column!r}")
        cursor.execute(
            f"SELECT {column} FROM reflexes WHERE session_id = ? AND {column} IS NOT NULL ORDER BY timestamp DESC LIMIT 1",
            (self.session_id,),
        )
        found = cursor.fetchone()
        return found[0] if found else default

    def _query_gate(self) -> GateStatus:
        """Pull sentinel gate state from database."""
        try:
            from empirica.data.session_database import SessionDatabase

            db = SessionDatabase()
            if db.conn is None:
                return GateStatus()

            cursor = db.conn.cursor()

            # Get latest phase
            cursor.execute(
                """
                SELECT phase, know, uncertainty, reflex_data, timestamp
                FROM reflexes
                WHERE session_id = ?
                ORDER BY timestamp DESC LIMIT 1
            """,
                (self.session_id,),
            )
            row = cursor.fetchone()

            if not row:
                db.close()
                return GateStatus(phase="no_preflight")

            phase, _know, _uncertainty, reflex_data, _ = row
            # Newest RECORDED know and uncertainty: the latest row is often a CHECK that omits one (NULL = not assessed). `x or default`
            # also read a genuine 0.0 as missing. Never recorded keeps the fail-closed defaults (know 0.0, uncertainty 1.0).
            know = self._latest_recorded(cursor, "know", 0.0)
            uncertainty = self._latest_recorded(cursor, "uncertainty", 1.0)

            # Determine decision from reflex_data
            decision = "unknown"
            transaction_id = None
            if reflex_data:
                import json

                try:
                    data = json.loads(reflex_data)
                    decision = data.get("decision", "unknown")
                    transaction_id = data.get("transaction_id")
                except Exception:
                    pass

            # Gate uses META UNCERTAINTY ONLY (2026-04-07).
            gate_passed = uncertainty <= 0.35
            db.close()

            return GateStatus(
                phase=phase.lower() if phase else "unknown",
                decision=decision,
                know=know,
                uncertainty=uncertainty,
                gate_passed=gate_passed,
                transaction_id=transaction_id,
            )
        except Exception as e:
            logger.debug(f"Gate query failed: {e}")
            return GateStatus()

    # --- Unified Query ---

    def get_system_status(self) -> SystemStatus:
        """
        Get complete system status snapshot.

        Combines push-cached state (from bus events) with pull-queried
        state (from subsystems that don't publish events).

        This is the single call that replaces querying 7 subsystems individually.
        """
        return SystemStatus(
            timestamp=time.time(),
            node=NodeIdentity(
                ai_id=self.node_id,
                session_id=self.session_id,
                uptime_seconds=time.time() - self.started_at,
                started_at=self.started_at,
            ),
            config=self._query_config(),
            memory=self._query_memory(),
            bus=self._query_bus(),
            attention=self._query_attention(),
            integrity=self._query_integrity(),
            gate=self._query_gate(),
        )


# --- Module-level accessors ---

_global_dashboard: SystemDashboard | None = None


def get_dashboard(
    session_id: str | None = None,
    node_id: str | None = None,
) -> SystemDashboard:
    """Get or create the global SystemDashboard instance."""
    global _global_dashboard
    if _global_dashboard is None:
        if session_id is None:
            raise ValueError("session_id required for first dashboard creation")
        _global_dashboard = SystemDashboard(
            session_id=session_id,
            node_id=node_id,
        )
    return _global_dashboard


def reset_dashboard():
    """Reset global dashboard (for testing)."""
    global _global_dashboard
    _global_dashboard = None
