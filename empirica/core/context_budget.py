#!/usr/bin/env python3
"""
Context Budget Manager - Token-level memory management for AI context windows.

The missing kernel subsystem of the Noetic OS. Manages allocation, eviction,
and injection of context items (MCO configs, findings, protocols, code) within
the finite context window, treating it as RAM with paging.

Three memory zones:
  ANCHOR  - Always-resident (CLAUDE.md, calibration, session IDs) ~15k tokens
  WORKING - Active task context (goals, findings, code) ~150k tokens
  CACHE   - Preloaded but evictable (protocols, historical findings) ~35k tokens

Sits on the EpistemicBus as an observer, reacting to:
  SESSION_STARTED      -> Initialize inventory, load anchor zone
  CONFIDENCE_DROPPED   -> Page fault: retrieve relevant items
  POSTFLIGHT_COMPLETE  -> Decay stale items, update references
  pre_compact          -> Triage for eviction before compaction

Human-tunable thresholds (like sysctl vm.* parameters):
  context.anchor_reserve       = 15000 tokens
  context.working_set_target   = 150000 tokens
  context.cache_limit          = 35000 tokens
  context.eviction_aggressiveness = 0.5
  context.decay_rate           = 0.1

Unix metaphor: Virtual memory manager with page replacement.

Usage:
    from empirica.core.context_budget import ContextBudgetManager, get_budget_manager

    manager = get_budget_manager(session_id="abc123")
    manager.register_item(ContextItem(...))
    manager.request_injection("ask_before_investigate", reason="uncertainty_spike")
    report = manager.get_budget_report()
"""

import json
import logging
import math
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from empirica.core.epistemic_bus import (
    EpistemicEvent,
    EpistemicObserver,
    EventTypes,
    get_global_bus,
)

logger = logging.getLogger(__name__)


# --- Memory Zones ---


class MemoryZone(str, Enum):
    """Context window memory zones (like Linux memory zones)."""

    ANCHOR = "anchor"  # Non-evictable, always resident
    WORKING = "working"  # Active task context, managed by priority
    CACHE = "cache"  # Preloaded, evicted first under pressure


class ContentType(str, Enum):
    """Types of content that occupy context window space."""

    CALIBRATION = "calibration"
    PROTOCOL = "protocol"
    FINDING = "finding"
    UNKNOWN = "unknown"
    DEAD_END = "dead_end"
    GOAL = "goal"
    CODE = "code"
    CONVERSATION = "conversation"
    SKILL = "skill"
    BOOTSTRAP = "bootstrap"
    SYSTEM_PROMPT = "system_prompt"


class InjectionChannel(str, Enum):
    """How content gets injected into context."""

    HOOK = "hook"
    SKILL = "skill"
    MCP = "mcp"
    DIRECT = "direct"
    IMPLICIT = "implicit"


# --- Extended Event Types ---


class BudgetEventTypes:
    """Event types published by the Context Budget Manager."""

    MEMORY_PRESSURE = "memory_pressure"
    CONTEXT_EVICTED = "context_evicted"
    CONTEXT_INJECTED = "context_injected"
    PAGE_FAULT = "page_fault"
    BUDGET_EXHAUSTED = "budget_exhausted"
    EVICTION_RECOMMENDED = "eviction_recommended"


# --- Data Models ---


@dataclass
class ContextItem:
    """A single item occupying space in the context window.

    Like a memory page: has an address (id), size (estimated_tokens),
    priority (for replacement), and zone (anchor/working/cache).
    """

    id: str
    zone: MemoryZone
    content_type: ContentType
    source: str
    channel: InjectionChannel
    label: str
    estimated_tokens: int

    # Priority components
    epistemic_value: float = 0.5
    recency: float = 1.0
    reference_count: int = 0

    # Lifecycle
    injected_at: float = field(default_factory=time.time)
    last_referenced: float = field(default_factory=time.time)
    evictable: bool = True

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def age(self) -> float:
        """Seconds since injection."""
        return time.time() - self.injected_at

    @property
    def idle_time(self) -> float:
        """Seconds since last reference."""
        return time.time() - self.last_referenced

    def compute_priority(self, decay_rate: float = 0.1) -> float:
        """Compute eviction priority score.

        Higher score = more important = evict last.
        priority = epistemic_value * recency_decay * log(1 + refs) * zone_weight
        """
        zone_weights = {
            MemoryZone.ANCHOR: 100.0,
            MemoryZone.WORKING: 1.0,
            MemoryZone.CACHE: 0.5,
        }
        zone_weight = zone_weights.get(self.zone, 1.0)

        # Recency decay based on idle time (per-minute)
        recency_factor = math.exp(-decay_rate * self.idle_time / 60.0)

        # Reference boost (logarithmic diminishing returns)
        ref_factor = math.log(1 + self.reference_count) + 1.0

        return self.epistemic_value * recency_factor * ref_factor * zone_weight

    def touch(self):
        """Mark item as recently referenced (like accessing a memory page)."""
        self.last_referenced = time.time()
        self.reference_count += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "zone": self.zone.value,
            "content_type": self.content_type.value,
            "source": self.source,
            "channel": self.channel.value,
            "label": self.label,
            "estimated_tokens": self.estimated_tokens,
            "epistemic_value": self.epistemic_value,
            "reference_count": self.reference_count,
            "evictable": self.evictable,
            "priority": self.compute_priority(),
            "age_seconds": round(self.age, 1),
            "idle_seconds": round(self.idle_time, 1),
        }


@dataclass
class BudgetThresholds:
    """Human-tunable thresholds for context budget management.

    Like sysctl vm.* parameters in Linux.
    """

    total_capacity: int = 200000
    anchor_reserve: int = 15000
    working_set_target: int = 150000
    cache_limit: int = 35000
    eviction_aggressiveness: float = 0.5
    decay_rate: float = 0.1
    min_priority_threshold: float = 0.05
    page_fault_retrieval_limit: int = 5
    pressure_threshold: float = 0.85

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BudgetThresholds":
        """Create from dictionary, ignoring unknown keys."""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BudgetReport:
    """Snapshot of current context budget state (like /proc/meminfo)."""

    timestamp: float
    session_id: str
    total_capacity: int
    total_used: int
    total_available: int
    utilization: float
    anchor_used: int
    anchor_limit: int
    working_used: int
    working_target: int
    cache_used: int
    cache_limit: int
    total_items: int
    anchor_items: int
    working_items: int
    cache_items: int
    under_pressure: bool
    eviction_candidates: int
    top_items: list[dict[str, Any]]
    bottom_items: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class EvictionResult:
    """Result of an eviction operation."""

    evicted_items: list[ContextItem]
    tokens_freed: int
    reason: str
    triggered_by: str


@dataclass
class InjectionRequest:
    """Request to inject content into context window."""

    content_id: str
    reason: str
    content_type: ContentType
    preferred_channel: InjectionChannel
    estimated_tokens: int = 0
    epistemic_value: float = 0.5
    priority: str = "normal"  # "critical" | "normal" | "low"
    metadata: dict[str, Any] = field(default_factory=dict)


# --- The Manager ---


class ContextBudgetManager(EpistemicObserver):
    """
    Token-level memory manager for the AI context window.

    Tracks what's in context, scores items by epistemic priority,
    evicts low-value items under pressure, and handles page faults
    by retrieving relevant items through injection channels.

    Implements EpistemicObserver to react to bus events.
    """

    def __init__(
        self,
        session_id: str,
        thresholds: BudgetThresholds | None = None,
        auto_subscribe: bool = True,
        node_id: str | None = None,
    ):
        self.session_id = session_id
        self.node_id = node_id or os.getenv("EMPIRICA_AI_ID", "unknown")
        self.thresholds = thresholds or BudgetThresholds()
        self._inventory: dict[str, ContextItem] = {}
        self._eviction_log: list[EvictionResult] = []
        self._injection_handlers: dict[InjectionChannel, Callable] = {}
        self._page_fault_count = 0
        self._eviction_count = 0
        self.created_at = time.time()

        if auto_subscribe:
            try:
                bus = get_global_bus()
                bus.subscribe(self)
                logger.info("ContextBudgetManager subscribed to EpistemicBus")
            except Exception as e:
                logger.warning(f"Could not subscribe to bus: {e}")

    # --- EpistemicObserver Interface ---

    def handle_event(self, event: EpistemicEvent) -> None:
        """React to epistemic events on the bus."""
        handlers = {
            EventTypes.SESSION_STARTED: self._on_session_started,
            EventTypes.CONFIDENCE_DROPPED: self._on_confidence_dropped,
            EventTypes.POSTFLIGHT_COMPLETE: self._on_postflight_complete,
            EventTypes.CALIBRATION_DRIFT_DETECTED: self._on_drift_detected,
            EventTypes.GOAL_CREATED: self._on_goal_created,
            EventTypes.GOAL_COMPLETED: self._on_goal_completed,
        }

        handler = handlers.get(event.event_type)
        if handler:
            try:
                handler(event)
            except Exception as e:
                logger.error(f"Budget manager failed on {event.event_type}: {e}")

    def _on_session_started(self, event: EpistemicEvent):
        """Initialize budget on session start."""
        logger.info(f"Budget manager: session {event.session_id} started")

    def _on_confidence_dropped(self, event: EpistemicEvent):
        """Page fault: confidence dropped, need more context."""
        self._page_fault_count += 1
        drop_data = event.data
        vector = drop_data.get("vector", "know")
        value = drop_data.get("value", 0.0)

        logger.info(f"Budget manager: page fault - {vector} dropped to {value:.2f}")

        if vector in ("know", "context"):
            self._request_bootstrap_injection(reason=f"{vector}_dropped_to_{value:.2f}")
        elif vector == "uncertainty":
            self._request_protocol_injection("ask_before_investigate", reason=f"uncertainty_spike_{value:.2f}")

    def _on_postflight_complete(self, event: EpistemicEvent):
        """Decay stale items after postflight for session {event.session_id}."""
        self._decay_all_items()
        self._check_pressure()

    def _on_drift_detected(self, event: EpistemicEvent):
        """Inject relevant protocols on calibration drift."""
        self._request_protocol_injection("epistemic_conduct", reason=f"calibration_drift_detected_{event.session_id}")

    def _on_goal_created(self, event: EpistemicEvent):
        """Register new goal as working set item."""
        goal_data = event.data
        self.register_item(
            ContextItem(
                id=f"goal_{goal_data.get('goal_id', uuid.uuid4().hex[:8])}",
                zone=MemoryZone.WORKING,
                content_type=ContentType.GOAL,
                source="goals-create",
                channel=InjectionChannel.MCP,
                label=goal_data.get("objective", "Unknown goal")[:80],
                estimated_tokens=200,
                epistemic_value=0.8,
                evictable=False,
            )
        )

    def _on_goal_completed(self, event: EpistemicEvent):
        """Move completed goal from working to cache."""
        goal_id = f"goal_{event.data.get('goal_id', '')}"
        item = self._inventory.get(goal_id)
        if item:
            item.zone = MemoryZone.CACHE
            item.evictable = True
            item.epistemic_value *= 0.3
            logger.info(f"Goal {goal_id} moved to cache zone")

    # --- Item Management ---

    def register_item(self, item: ContextItem) -> bool:
        """Register an item as present in the context window.

        Like mapping a page into the process address space.
        Returns True if registered, False if rejected (budget exceeded).
        """
        zone_budget = self._get_zone_budget(item.zone)
        zone_used = self._get_zone_usage(item.zone)

        if zone_used + item.estimated_tokens > zone_budget:
            needed = (zone_used + item.estimated_tokens) - zone_budget
            evicted = self._evict_from_zone(item.zone, needed, reason="make_room")

            if evicted.tokens_freed < needed:
                logger.warning(
                    f"Cannot register {item.label}: zone {item.zone.value} full "
                    f"({zone_used}/{zone_budget} tokens, need {item.estimated_tokens})"
                )
                return False

        self._inventory[item.id] = item
        logger.debug(f"Registered: {item.label} ({item.estimated_tokens}t, {item.zone.value})")
        return True

    def unregister_item(self, item_id: str) -> ContextItem | None:
        """Remove an item from the inventory (no longer in context)."""
        item = self._inventory.pop(item_id, None)
        if item:
            logger.debug(f"Unregistered: {item.label}")
        return item

    def touch_item(self, item_id: str):
        """Mark an item as recently referenced (updates LRU)."""
        item = self._inventory.get(item_id)
        if item:
            item.touch()

    def find_items(
        self,
        zone: MemoryZone | None = None,
        content_type: ContentType | None = None,
        min_priority: float | None = None,
    ) -> list[ContextItem]:
        """Find items matching criteria."""
        results = []
        for item in self._inventory.values():
            if zone and item.zone != zone:
                continue
            if content_type and item.content_type != content_type:
                continue
            if min_priority is not None:
                if item.compute_priority(self.thresholds.decay_rate) < min_priority:
                    continue
            results.append(item)
        return results

    # --- Eviction ---

    def evict_lowest_priority(self, tokens_needed: int, reason: str = "pressure") -> EvictionResult:
        """Evict lowest-priority items to free space.

        Page replacement algorithm: score all evictable items,
        remove lowest-priority first until enough space freed.
        """
        evictable = [item for item in self._inventory.values() if item.evictable]
        evictable.sort(key=lambda i: i.compute_priority(self.thresholds.decay_rate))

        evicted = []
        freed = 0

        for item in evictable:
            if freed >= tokens_needed:
                break
            evicted.append(item)
            freed += item.estimated_tokens
            self._inventory.pop(item.id, None)

        result = EvictionResult(
            evicted_items=evicted,
            tokens_freed=freed,
            reason=reason,
            triggered_by="evict_lowest_priority",
        )

        if evicted:
            self._eviction_count += len(evicted)
            self._eviction_log.append(result)
            logger.info(f"Evicted {len(evicted)} items, freed {freed} tokens (reason: {reason})")
            self._publish_event(
                BudgetEventTypes.CONTEXT_EVICTED,
                {
                    "items_evicted": len(evicted),
                    "tokens_freed": freed,
                    "reason": reason,
                    "evicted_labels": [i.label[:50] for i in evicted],
                },
            )

        return result

    def _evict_from_zone(self, zone: MemoryZone, tokens_needed: int, reason: str) -> EvictionResult:
        """Evict from a specific zone."""
        evictable = [item for item in self._inventory.values() if item.zone == zone and item.evictable]
        evictable.sort(key=lambda i: i.compute_priority(self.thresholds.decay_rate))

        evicted = []
        freed = 0

        for item in evictable:
            if freed >= tokens_needed:
                break
            evicted.append(item)
            freed += item.estimated_tokens
            self._inventory.pop(item.id, None)

        result = EvictionResult(
            evicted_items=evicted,
            tokens_freed=freed,
            reason=reason,
            triggered_by=f"evict_from_{zone.value}",
        )

        if evicted:
            self._eviction_count += len(evicted)
            self._eviction_log.append(result)

        return result

    # --- Pressure Management ---

    def _check_pressure(self):
        """Check if under memory pressure and act if needed."""
        report = self.get_budget_report()

        if report.utilization >= self.thresholds.pressure_threshold:
            logger.warning(
                f"Memory pressure: {report.utilization:.1%} utilization "
                f"({report.total_used}/{report.total_capacity} tokens)"
            )
            self._publish_event(
                BudgetEventTypes.MEMORY_PRESSURE,
                {
                    "utilization": report.utilization,
                    "total_used": report.total_used,
                    "total_capacity": report.total_capacity,
                    "eviction_candidates": report.eviction_candidates,
                },
            )

            if self.thresholds.eviction_aggressiveness > 0.5:
                target = int(report.total_capacity * 0.7)
                to_free = report.total_used - target
                if to_free > 0:
                    self.evict_lowest_priority(to_free, reason="auto_pressure_relief")

    def _decay_all_items(self):
        """Apply time decay to all items (like aging pages)."""
        evict_candidates = []

        for item in self._inventory.values():
            if not item.evictable:
                continue
            priority = item.compute_priority(self.thresholds.decay_rate)
            if priority < self.thresholds.min_priority_threshold:
                evict_candidates.append(item)

        for item in evict_candidates:
            self._inventory.pop(item.id, None)
            logger.debug(f"Decayed and evicted: {item.label}")

        if evict_candidates:
            self._eviction_count += len(evict_candidates)
            logger.info(f"Decay pass: evicted {len(evict_candidates)} stale items")

    # --- Injection Requests ---

    def request_injection(self, request: InjectionRequest) -> bool:
        """Request injection of content into context window.

        Checks budget, routes to injection channel handler.
        Returns True if approved, False if rejected.
        """
        total_used = self._get_total_usage()
        if total_used + request.estimated_tokens > self.thresholds.total_capacity:
            if request.priority != "critical":
                logger.info(
                    f"Injection rejected: {request.content_id} ({request.estimated_tokens}t would exceed budget)"
                )
                return False
            else:
                self.evict_lowest_priority(request.estimated_tokens, reason=f"critical_injection_{request.content_id}")

        handler = self._injection_handlers.get(request.preferred_channel)
        if handler:
            try:
                handler(request)
            except Exception as e:
                logger.error(f"Injection handler failed: {e}")
                return False

        zone = MemoryZone.WORKING if request.priority == "critical" else MemoryZone.CACHE
        item = ContextItem(
            id=request.content_id,
            zone=zone,
            content_type=request.content_type,
            source=request.content_id,
            channel=request.preferred_channel,
            label=request.content_id,
            estimated_tokens=request.estimated_tokens,
            epistemic_value=request.epistemic_value,
            metadata=request.metadata,
        )
        self.register_item(item)

        self._publish_event(
            BudgetEventTypes.CONTEXT_INJECTED,
            {
                "content_id": request.content_id,
                "reason": request.reason,
                "tokens": request.estimated_tokens,
                "channel": request.preferred_channel.value,
            },
        )
        return True

    def register_injection_handler(
        self,
        channel: InjectionChannel,
        handler: Callable[[InjectionRequest], None],
    ):
        """Register a handler for an injection channel."""
        self._injection_handlers[channel] = handler
        logger.info(f"Registered injection handler for {channel.value}")

    def _request_bootstrap_injection(self, reason: str):
        """Request project bootstrap data injection."""
        self.request_injection(
            InjectionRequest(
                content_id="project_bootstrap",
                reason=reason,
                content_type=ContentType.BOOTSTRAP,
                preferred_channel=InjectionChannel.MCP,
                estimated_tokens=5000,
                epistemic_value=0.8,
            )
        )

    def _request_protocol_injection(self, protocol_name: str, reason: str):
        """Request MCO protocol injection via skill channel."""
        token_estimates = {
            "epistemic_conduct": 3000,
            "ask_before_investigate": 1500,
            "model_profiles": 2000,
            "personas": 2500,
            "protocols": 1000,
        }
        self.request_injection(
            InjectionRequest(
                content_id=f"protocol_{protocol_name}",
                reason=reason,
                content_type=ContentType.PROTOCOL,
                preferred_channel=InjectionChannel.SKILL,
                estimated_tokens=token_estimates.get(protocol_name, 2000),
                epistemic_value=0.7,
                metadata={"protocol_name": protocol_name},
            )
        )

    # --- Zone Accounting ---

    def _get_zone_budget(self, zone: MemoryZone) -> int:
        """Get token budget for a zone."""
        budgets = {
            MemoryZone.ANCHOR: self.thresholds.anchor_reserve,
            MemoryZone.WORKING: self.thresholds.working_set_target,
            MemoryZone.CACHE: self.thresholds.cache_limit,
        }
        return budgets.get(zone, 0)

    def _get_zone_usage(self, zone: MemoryZone) -> int:
        """Get current token usage for a zone."""
        return sum(item.estimated_tokens for item in self._inventory.values() if item.zone == zone)

    def _get_total_usage(self) -> int:
        """Get total token usage across all zones."""
        return sum(item.estimated_tokens for item in self._inventory.values())

    # --- Reporting ---

    def get_budget_report(self) -> BudgetReport:
        """Generate a complete budget report (like /proc/meminfo)."""
        total_used = self._get_total_usage()
        anchor_used = self._get_zone_usage(MemoryZone.ANCHOR)
        working_used = self._get_zone_usage(MemoryZone.WORKING)
        cache_used = self._get_zone_usage(MemoryZone.CACHE)

        all_items = list(self._inventory.values())
        scored = [(item, item.compute_priority(self.thresholds.decay_rate)) for item in all_items]
        scored.sort(key=lambda x: x[1], reverse=True)

        eviction_candidates = sum(1 for _, score in scored if score < self.thresholds.min_priority_threshold)
        utilization = total_used / max(self.thresholds.total_capacity, 1)

        return BudgetReport(
            timestamp=time.time(),
            session_id=self.session_id,
            total_capacity=self.thresholds.total_capacity,
            total_used=total_used,
            total_available=max(0, self.thresholds.total_capacity - total_used),
            utilization=utilization,
            anchor_used=anchor_used,
            anchor_limit=self.thresholds.anchor_reserve,
            working_used=working_used,
            working_target=self.thresholds.working_set_target,
            cache_used=cache_used,
            cache_limit=self.thresholds.cache_limit,
            total_items=len(all_items),
            anchor_items=len([i for i in all_items if i.zone == MemoryZone.ANCHOR]),
            working_items=len([i for i in all_items if i.zone == MemoryZone.WORKING]),
            cache_items=len([i for i in all_items if i.zone == MemoryZone.CACHE]),
            under_pressure=utilization >= self.thresholds.pressure_threshold,
            eviction_candidates=eviction_candidates,
            top_items=[item.to_dict() for item, _ in scored[:5]],
            bottom_items=([item.to_dict() for item, _ in scored[-5:]] if scored else []),
        )

    def get_inventory_summary(self) -> dict[str, Any]:
        """Quick inventory summary for statusline or monitoring."""
        total = self._get_total_usage()
        cap = self.thresholds.total_capacity
        return {
            "node_id": self.node_id,
            "tokens_used": total,
            "tokens_available": max(0, cap - total),
            "utilization_pct": round(total / max(cap, 1) * 100, 1),
            "item_count": len(self._inventory),
            "zones": {
                "anchor": self._get_zone_usage(MemoryZone.ANCHOR),
                "working": self._get_zone_usage(MemoryZone.WORKING),
                "cache": self._get_zone_usage(MemoryZone.CACHE),
            },
            "page_faults": self._page_fault_count,
            "evictions": self._eviction_count,
        }

    # --- Bus Publishing ---

    def _publish_event(self, event_type: str, data: dict[str, Any]):
        """Publish event on the epistemic bus."""
        try:
            bus = get_global_bus()
            bus.publish(
                EpistemicEvent(
                    event_type=event_type,
                    agent_id=f"cbm:{self.node_id}",
                    session_id=self.session_id,
                    data={**data, "node_id": self.node_id},
                )
            )
        except Exception as e:
            logger.debug(f"Could not publish event: {e}")

    # --- Persistence ---

    def persist_state(self) -> bool:
        """Persist budget state to database for cross-session continuity."""
        try:
            from empirica.data.session_database import SessionDatabase

            db = SessionDatabase()
            if db.conn is None:
                logger.error("No database connection")
                return False
            cursor = db.conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS context_budget_state (
                    session_id TEXT PRIMARY KEY,
                    node_id TEXT,
                    inventory_json TEXT,
                    thresholds_json TEXT,
                    page_faults INTEGER,
                    evictions INTEGER,
                    created_at REAL,
                    updated_at REAL
                )
            """)

            inventory_data = {item_id: item.to_dict() for item_id, item in self._inventory.items()}

            cursor.execute(
                """
                INSERT OR REPLACE INTO context_budget_state
                (session_id, node_id, inventory_json, thresholds_json,
                 page_faults, evictions, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    self.session_id,
                    self.node_id,
                    json.dumps(inventory_data),
                    json.dumps(self.thresholds.to_dict()),
                    self._page_fault_count,
                    self._eviction_count,
                    self.created_at,
                    time.time(),
                ),
            )

            db.conn.commit()
            db.close()
            return True
        except Exception as e:
            logger.error(f"Failed to persist budget state: {e}")
            return False


# --- Token Estimation ---


def estimate_tokens(text: str) -> int:
    """Estimate token count for a text string.

    Heuristic: ~4 characters per token for English text.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate ``text`` to ``max_chars`` at a word boundary, appending an
    overflow marker.

    Non-strings and already-short strings pass through unchanged. This is the
    single source of truth for display-string truncation at context-injection
    sites (pattern retrieval, bootstrap global-learnings) — keeping one
    algorithm avoids the two-sources-of-truth drift that a per-site copy
    invites.
    """
    if not isinstance(text, str) or len(text) <= max_chars:
        return text
    head = text[:max_chars].rsplit(" ", 1)[0].rstrip()
    return f"{head}… (+{len(text) - len(head)} chars)"


# --- Threshold Loading ---


def load_thresholds_from_config() -> BudgetThresholds:
    """Load budget thresholds from MCO config directory."""
    from pathlib import Path

    config_path = Path(__file__).parent.parent / "config" / "mco" / "context_budget.yaml"

    if config_path.exists():
        try:
            import yaml

            with open(config_path) as f:
                data = yaml.safe_load(f) or {}
            thresholds_data = data.get("context_budget", data)
            return BudgetThresholds.from_dict(thresholds_data)
        except Exception as e:
            logger.warning(f"Failed to load context_budget.yaml: {e}")

    return BudgetThresholds()


# --- Singleton ---

_global_manager: ContextBudgetManager | None = None


def get_budget_manager(
    session_id: str | None = None,
    thresholds: BudgetThresholds | None = None,
    node_id: str | None = None,
) -> ContextBudgetManager:
    """Get or create the global Context Budget Manager."""
    global _global_manager

    if _global_manager is None:
        if session_id is None:
            raise ValueError("session_id required for first creation of budget manager")
        if thresholds is None:
            thresholds = load_thresholds_from_config()

        _global_manager = ContextBudgetManager(
            session_id=session_id,
            thresholds=thresholds,
            node_id=node_id,
        )
        logger.info(f"Created global ContextBudgetManager for session {session_id}")

    return _global_manager


def reset_budget_manager():
    """Reset global manager (for testing or session transitions)."""
    global _global_manager
    if _global_manager:
        _global_manager.persist_state()
    _global_manager = None
