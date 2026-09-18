#!/usr/bin/env python3
"""
Bus Persistence - Durable event storage for cross-node EpistemicBus.

Promotes the in-process EpistemicBus to a distributed event fabric by persisting
events to storage backends. This enables cross-node event discovery in the NUMA
cluster model: each AI node has its own bus, but events persist to shared storage
where other nodes can query them.

Two storage backends (both implement EpistemicObserver):
  SqliteBusObserver  - Always available, writes to sessions.db events table
  QdrantBusObserver  - Optional, writes to Qdrant collection for semantic search

The SQLite observer is the guaranteed fallback (like journaling to disk).
The Qdrant observer enables semantic event retrieval across nodes.

Usage:
    from empirica.core.bus_persistence import wire_persistent_observers
    wire_persistent_observers(session_id="abc123")

    # Events now persist to both SQLite and Qdrant (if available)
    bus = get_global_bus()
    bus.publish(event)  # Goes to in-process + SQLite + Qdrant
"""

import json
import logging
import os
import uuid
from typing import Any

from empirica.core.epistemic_bus import (
    EpistemicEvent,
    EpistemicObserver,
    get_global_bus,
)
from empirica.data.schema.lazy_tables_schema import LAZY_TABLE_DDL

logger = logging.getLogger(__name__)


class SqliteBusObserver(EpistemicObserver):
    """
    Persist bus events to SQLite for durable cross-session event log.

    Always available - the guaranteed fallback when Qdrant is down.
    Writes to `epistemic_events` table in sessions.db.

    Like journaling to disk: slow but reliable.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._ensure_table()
        self._event_count = 0

    def _ensure_table(self):
        """Create events table if it doesn't exist."""
        try:
            from empirica.data.session_database import SessionDatabase

            db = SessionDatabase()
            if db.conn is None:
                return
            db.conn.execute(LAZY_TABLE_DDL["epistemic_events"])
            db.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_session
                ON epistemic_events(session_id, timestamp)
            """)
            db.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_type
                ON epistemic_events(event_type, timestamp)
            """)
            db.conn.commit()
            db.close()
        except Exception as e:
            logger.warning(f"Could not ensure events table: {e}")

    def handle_event(self, event: EpistemicEvent) -> None:
        """Persist event to SQLite."""
        try:
            from empirica.data.session_database import SessionDatabase

            db = SessionDatabase()
            if db.conn is None:
                return

            event_id = str(uuid.uuid4())
            node_id = os.getenv("EMPIRICA_AI_ID", "unknown")

            db.conn.execute(
                """
                INSERT INTO epistemic_events
                (id, session_id, event_type, agent_id, data_json, timestamp, node_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    event_id,
                    event.session_id,
                    event.event_type,
                    event.agent_id,
                    json.dumps(event.data),
                    event.timestamp,
                    node_id,
                ),
            )
            db.conn.commit()
            db.close()
            self._event_count += 1
        except Exception as e:
            logger.debug(f"SQLite event persist failed: {e}")

    @staticmethod
    def query_events(
        session_id: str | None = None,
        event_type: str | None = None,
        since: float | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query persisted events from SQLite.

        Enables cross-session event discovery.
        """
        try:
            from empirica.data.session_database import SessionDatabase

            db = SessionDatabase()
            if db.conn is None:
                return []

            query = "SELECT * FROM epistemic_events WHERE 1=1"
            params: list = []

            if session_id:
                query += " AND session_id = ?"
                params.append(session_id)
            if event_type:
                query += " AND event_type = ?"
                params.append(event_type)
            if since:
                query += " AND timestamp > ?"
                params.append(since)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor = db.conn.execute(query, params)
            columns = [desc[0] for desc in cursor.description]
            results = [dict(zip(columns, row)) for row in cursor.fetchall()]
            db.close()
            return results
        except Exception as e:
            logger.debug(f"SQLite event query failed: {e}")
            return []


class QdrantBusObserver(EpistemicObserver):
    """
    Persist bus events to Qdrant for semantic cross-node discovery.

    Optional - gracefully degrades if Qdrant is unavailable.
    Writes to `epistemic_events` collection with event embeddings.

    Like network-attached storage: enables cross-node event queries
    with semantic similarity (not just exact match).
    """

    COLLECTION_NAME = "epistemic_events"

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._available = self._check_available()
        self._event_count = 0

    def _check_available(self) -> bool:
        """Check if Qdrant is available."""
        try:
            from empirica.core.qdrant.vector_store import _check_qdrant_available

            return _check_qdrant_available()
        except Exception:
            return False

    def _ensure_collection(self):
        """Create events collection if needed."""
        try:
            from empirica.core.qdrant.connection import _ensure_collection_matches_vector
            from empirica.core.qdrant.vector_store import (
                _get_qdrant_client,
                _get_vector_size,
            )

            client = _get_qdrant_client()
            if client is None:
                self._available = False
                return

            _ensure_collection_matches_vector(
                client,
                self.COLLECTION_NAME,
                _get_vector_size(),
                create_if_missing=False,
            )
        except Exception as e:
            logger.debug(f"Could not ensure Qdrant collection: {e}")
            self._available = False

    def handle_event(self, event: EpistemicEvent) -> None:
        """Persist event to Qdrant with embedding."""
        if not self._available:
            return

        try:
            from qdrant_client.models import PointStruct

            from empirica.core.qdrant.connection import _get_embedding_for_collection
            from empirica.core.qdrant.vector_store import (
                _get_qdrant_client,
            )

            # Create searchable text from event
            event_text = f"{event.event_type}: {event.agent_id} {json.dumps(event.data)[:500]}"
            client = _get_qdrant_client()
            if client is None:
                return
            embedding = _get_embedding_for_collection(
                client,
                self.COLLECTION_NAME,
                event_text,
                create_if_missing=True,
            )
            if embedding is None:
                return

            point_id = str(uuid.uuid4())
            node_id = os.getenv("EMPIRICA_AI_ID", "unknown")

            client.upsert(
                collection_name=self.COLLECTION_NAME,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=embedding,
                        payload={
                            "event_type": event.event_type,
                            "agent_id": event.agent_id,
                            "session_id": event.session_id,
                            "data": event.data,
                            "timestamp": event.timestamp,
                            "node_id": node_id,
                        },
                    )
                ],
            )
            self._event_count += 1
        except Exception as e:
            logger.debug(f"Qdrant event persist failed: {e}")

    def query_semantic(
        self,
        query_text: str,
        limit: int = 10,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search over persisted events.

        The cross-node discovery mechanism: find events from other
        nodes that are semantically relevant to the current context.
        """
        if not self._available:
            return []

        try:
            from qdrant_client.models import FieldCondition, Filter, MatchValue

            from empirica.core.qdrant.connection import _get_embedding_for_collection
            from empirica.core.qdrant.vector_store import _get_qdrant_client

            client = _get_qdrant_client()
            if client is None:
                return []
            embedding = _get_embedding_for_collection(
                client,
                self.COLLECTION_NAME,
                query_text,
                create_if_missing=False,
            )
            if embedding is None:
                return []

            query_filter = None
            if event_type:
                query_filter = Filter(
                    must=[
                        FieldCondition(
                            key="event_type",
                            match=MatchValue(value=event_type),
                        ),
                    ]
                )

            results = client.query_points(
                collection_name=self.COLLECTION_NAME,
                query=embedding,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )

            return [
                {
                    "score": point.score,
                    **point.payload,
                }
                for point in results.points
                if point.payload
            ]
        except Exception as e:
            logger.debug(f"Qdrant semantic search failed: {e}")
            return []


def wire_persistent_observers(session_id: str) -> dict[str, bool]:
    """
    Wire persistent observers into the global bus.

    Call this once per session to enable event persistence.

    Returns dict indicating which backends were wired:
        {"sqlite": True, "qdrant": True/False}
    """
    bus = get_global_bus()
    result = {"sqlite": False, "qdrant": False}

    # Always wire SQLite (guaranteed available)
    try:
        sqlite_observer = SqliteBusObserver(session_id)
        bus.subscribe(sqlite_observer)
        result["sqlite"] = True
        logger.info("Wired SqliteBusObserver to EpistemicBus")
    except Exception as e:
        logger.warning(f"Failed to wire SQLite observer: {e}")

    # Wire Qdrant if available (optional, cross-node)
    try:
        qdrant_observer = QdrantBusObserver(session_id)
        if qdrant_observer._available:
            bus.subscribe(qdrant_observer)
            result["qdrant"] = True
            logger.info("Wired QdrantBusObserver to EpistemicBus")
        else:
            logger.debug("Qdrant not available, skipping QdrantBusObserver")
    except Exception as e:
        logger.debug(f"Failed to wire Qdrant observer: {e}")

    return result
