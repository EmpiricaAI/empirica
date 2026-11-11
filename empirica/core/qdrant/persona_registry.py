"""
PersonaRegistry - Store and Discover Personas in Qdrant

Enables semantic search and discovery of personas by domain, capability,
reputation, and other dimensions. Each persona is stored as a 13D vector
in Qdrant's vector database with rich metadata.

Key Features:
- Store persona's epistemic vectors for semantic search
- Filter by focus domains, tags, persona type
- Find similar personas (by epistemic profile)
- Track reputation scores
- Cross-reference with public keys for signature verification

Design:
    registry = PersonaRegistry()  # the store every empirica client resolves
    registry.register_persona(signing_persona)

    # Find security experts
    security_personas = registry.find_personas_by_domain("security", limit=5)

    # Find similar epistemic profiles
    similar = registry.find_similar_personas(researcher_persona)

    # Get all registered personas
    all_personas = registry.list_all_personas()
"""

import logging
from hashlib import sha256
from typing import Any, ClassVar

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from empirica.core.persona.signing_persona import SigningPersona

logger = logging.getLogger(__name__)


class PersonaRegistry:
    """
    Store and discover personas in Qdrant vector database

    Uses 13D epistemic vectors for semantic search and filtering.
    Each persona is registered with:
    - Epistemic priors (13 vectors)
    - Public key (for signature verification)
    - Focus domains (search tags)
    - Reputation score
    - Metadata (tags, created_at, etc.)

    Usage:
        registry = PersonaRegistry()

        # Register a persona
        registry.register_persona(signing_persona)

        # Find personas by domain
        security = registry.find_personas_by_domain("security")
        for persona in security:
            print(f"{persona['name']}: {persona['public_key'][:16]}...")

        # Find similar epistemic profiles
        similar = registry.find_similar_personas(researcher_persona, limit=3)

        # Get persona by ID
        persona = registry.get_persona_by_id("researcher_v1.0.0")

        # List all personas
        all_personas = registry.list_all_personas()
    """

    # Collection name in Qdrant
    COLLECTION_NAME = "personas"

    # Vector size (13 epistemic dimensions)
    VECTOR_SIZE = 13

    # Epistemic vectors in order
    VECTOR_KEYS: ClassVar[list[str]] = [
        "engagement",
        "know",
        "do",
        "context",
        "clarity",
        "coherence",
        "signal",
        "density",
        "state",
        "change",
        "completion",
        "impact",
        "uncertainty",
    ]

    def __init__(
        self,
        qdrant_host: str | None = None,
        qdrant_port: int = 6333,
        prefer_grpc: bool = False,
        client: Any = None,
    ):
        """
        Initialize PersonaRegistry

        Args:
            qdrant_host: An explicit Qdrant host. When omitted, the store is the one every other empirica client
                resolves (the per-org resolver, ``EMPIRICA_QDRANT_URL``, a server on 6333, the local server's store).
            qdrant_port: Port for an explicit ``qdrant_host``
            prefer_grpc: gRPC for an explicit ``qdrant_host`` (off by default: the local server speaks REST only)
            client: An already-built client (tests, embedding callers)

        Raises:
            ConnectionError: If cannot connect to Qdrant
        """
        try:
            if client is not None:
                self.client = client
            elif qdrant_host:
                self.client = QdrantClient(host=qdrant_host, port=qdrant_port, prefer_grpc=prefer_grpc)
            else:
                from empirica.core.qdrant import connection

                resolved = connection._get_qdrant_client()
                if resolved is None:
                    raise ConnectionError("no Qdrant store answers (no server, no local server store)")
                self.client = resolved

            # Verify connection
            self.client.get_collections()

        except Exception as e:
            logger.error(f"Failed to connect to Qdrant: {e}")
            raise ConnectionError(f"Cannot connect to Qdrant: {e}") from e

        # Ensure collection exists
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        """Create collection if it doesn't exist"""
        try:
            # Check if collection exists
            self.client.get_collection(self.COLLECTION_NAME)
            logger.info(f"✓ Using existing Qdrant collection: {self.COLLECTION_NAME}")

        except Exception:
            # Collection doesn't exist, create it
            logger.info(f"Creating Qdrant collection: {self.COLLECTION_NAME}")

            self.client.create_collection(
                collection_name=self.COLLECTION_NAME,
                vectors_config=VectorParams(size=self.VECTOR_SIZE, distance=Distance.COSINE),
            )

            logger.info(f"✓ Created collection: {self.COLLECTION_NAME}")

    def register_persona(self, signing_persona: SigningPersona) -> int:
        """
        Register a persona in Qdrant

        Args:
            signing_persona: SigningPersona instance to register

        Returns:
            str: Point ID in Qdrant

        Raises:
            ValueError: If persona data invalid
        """
        try:
            # Extract epistemic priors as vector
            priors = signing_persona.persona.epistemic_config.priors
            vector = [priors[key] for key in self.VECTOR_KEYS]

            # Generate point ID from persona ID
            point_id = self._persona_id_to_point_id(signing_persona.persona.persona_id)

            # Prepare metadata
            public_persona = signing_persona.export_public_persona()
            metadata = {
                "persona_id": public_persona["persona_id"],
                "name": public_persona["name"],
                "version": public_persona["version"],
                "public_key": public_persona["public_key"],
                "persona_type": public_persona["persona_type"],
                "focus_domains": public_persona["epistemic_config"]["focus_domains"],
                "created_at": public_persona["created_at"],
                "reputation_score": public_persona["epistemic_config"]["priors"].get("uncertainty", 0.5),
                "tags": public_persona["metadata"].get("tags", []),
                "identity_ai_id": public_persona.get("identity_ai_id", ""),
            }

            # Create point
            point = PointStruct(id=point_id, vector=vector, payload=metadata)

            # Upsert point (insert or update)
            self.client.upsert(collection_name=self.COLLECTION_NAME, points=[point])

            logger.info(
                f"✓ Registered persona: {signing_persona.persona.persona_id} "
                f"ID={point_id} domains={metadata['focus_domains']}"
            )

            return point_id

        except Exception as e:
            logger.error(f"Failed to register persona: {e}")
            raise ValueError(f"Cannot register persona: {e}") from e

    def find_personas_by_domain(self, domain: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        Find personas focused on a specific domain

        Uses metadata filtering for exact domain matching.

        Args:
            domain: Domain name (e.g., "security", "performance")
            limit: Maximum results to return

        Returns:
            List of persona dicts with metadata and vectors

        Example:
            security_experts = registry.find_personas_by_domain("security")
            for expert in security_experts:
                print(f"{expert['name']}: {expert['public_key'][:16]}")
        """
        try:
            # Search with metadata filter
            results = self.client.scroll(
                collection_name=self.COLLECTION_NAME,
                limit=limit * 2,  # Get more and filter
            )

            # Filter for domain
            matching = []
            for point in results[0]:
                if domain in point.payload.get("focus_domains", []):
                    matching.append(self._point_to_persona_dict(point))

            # Limit results
            return matching[:limit]

        except Exception as e:
            logger.warning(f"Error finding personas by domain: {e}")
            return []

    def find_personas_by_tag(self, tag: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        Find personas by tag

        Args:
            tag: Tag to search for (e.g., "builtin", "expert", "specialist")
            limit: Maximum results

        Returns:
            List of matching personas
        """
        try:
            results = self.client.scroll(collection_name=self.COLLECTION_NAME, limit=limit * 2)

            matching = []
            for point in results[0]:
                if tag in point.payload.get("tags", []):
                    matching.append(self._point_to_persona_dict(point))

            return matching[:limit]

        except Exception as e:
            logger.warning(f"Error finding personas by tag: {e}")
            return []

    def find_similar_personas(
        self, signing_persona: SigningPersona, limit: int = 5, min_similarity: float = 0.7
    ) -> list[dict[str, Any]]:
        """
        Find personas with similar epistemic profiles

        Uses cosine similarity of epistemic vectors.

        Args:
            signing_persona: Reference persona
            limit: Maximum results
            min_similarity: Minimum similarity threshold

        Returns:
            List of similar personas (sorted by similarity)
        """
        try:
            # Get reference vector
            priors = signing_persona.persona.epistemic_config.priors
            query_vector = [priors[key] for key in self.VECTOR_KEYS]

            # Search similar (query_points: client.search() is gone in qdrant-client 1.18, goal 6f1e9bb6)
            results = self.client.query_points(
                collection_name=self.COLLECTION_NAME,
                query=query_vector,
                limit=limit + 1,  # +1 to exclude self
                with_payload=True,
            ).points

            similar = []
            for scored_point in results:
                payload = scored_point.payload or {}
                # Skip self
                if payload.get("persona_id") == signing_persona.persona.persona_id:
                    continue

                # Check threshold
                if scored_point.score >= min_similarity:
                    persona_dict = self._point_to_persona_dict(scored_point)
                    persona_dict["similarity_score"] = scored_point.score
                    similar.append(persona_dict)

            return similar[:limit]

        except Exception as e:
            logger.warning(f"Error finding similar personas: {e}")
            return []

    def get_persona_by_id(self, persona_id: str) -> dict[str, Any] | None:
        """
        Get persona by ID

        Args:
            persona_id: Persona identifier

        Returns:
            Persona dict or None if not found
        """
        try:
            point_id = self._persona_id_to_point_id(persona_id)
            point = self.client.retrieve(collection_name=self.COLLECTION_NAME, ids=[point_id])

            if not point:
                return None

            return self._point_to_persona_dict(point[0])

        except Exception as e:
            logger.warning(f"Error retrieving persona {persona_id}: {e}")
            return None

    def list_all_personas(self) -> list[dict[str, Any]]:
        """
        List all registered personas

        Returns:
            List of all persona dicts
        """
        try:
            results = self.client.scroll(
                collection_name=self.COLLECTION_NAME,
                limit=10000,  # Reasonable limit
            )

            personas = [self._point_to_persona_dict(point) for point in results[0]]

            logger.info(f"✓ Listed {len(personas)} personas")
            return personas

        except Exception as e:
            logger.warning(f"Error listing personas: {e}")
            return []

    def get_personas_by_type(self, persona_type: str) -> list[dict[str, Any]]:
        """
        Get personas by type (security, ux, performance, etc.)

        Args:
            persona_type: Type to filter by

        Returns:
            List of personas of that type
        """
        try:
            results = self.client.scroll(collection_name=self.COLLECTION_NAME, limit=10000)

            matching = [
                self._point_to_persona_dict(point)
                for point in results[0]
                if point.payload.get("persona_type") == persona_type
            ]

            return matching

        except Exception as e:
            logger.warning(f"Error getting personas by type: {e}")
            return []

    def get_personas_by_reputation(
        self, min_reputation: float = 0.0, max_reputation: float = 1.0, limit: int = 10
    ) -> list[dict[str, Any]]:
        """
        Get high-reputation personas

        Args:
            min_reputation: Minimum reputation score
            max_reputation: Maximum reputation score
            limit: Maximum results

        Returns:
            List of personas sorted by reputation (highest first)
        """
        try:
            all_personas = self.list_all_personas()

            # Filter by reputation
            filtered = [p for p in all_personas if min_reputation <= p.get("reputation_score", 0.5) <= max_reputation]

            # Sort by reputation (descending)
            filtered.sort(key=lambda x: x.get("reputation_score", 0.5), reverse=True)

            return filtered[:limit]

        except Exception as e:
            logger.warning(f"Error getting personas by reputation: {e}")
            return []

    def delete_persona(self, persona_id: str) -> bool:
        """
        Delete a persona from registry

        Args:
            persona_id: Persona to delete

        Returns:
            bool: True if deleted successfully
        """
        try:
            point_id = self._persona_id_to_point_id(persona_id)
            self.client.delete(collection_name=self.COLLECTION_NAME, points_selector=point_id)

            logger.info(f"✓ Deleted persona: {persona_id}")
            return True

        except Exception as e:
            logger.warning(f"Error deleting persona: {e}")
            return False

    def get_registry_stats(self) -> dict[str, Any]:
        """
        Get registry statistics

        Returns:
            Dict with collection stats
        """
        try:
            collection_info = self.client.get_collection(self.COLLECTION_NAME)

            return {
                "collection": self.COLLECTION_NAME,
                "total_personas": collection_info.points_count,
                "vectors_size": self.VECTOR_SIZE,
                "vector_keys": self.VECTOR_KEYS,
            }

        except Exception as e:
            logger.warning(f"Error getting registry stats: {e}")
            return {}

    def register_agent(self, agent_package: dict[str, Any]) -> str:
        """
        Register an epistemic agent from export package format.

        This enables the sharing network - agents exported with agent-export
        can be registered for discovery by other users.

        Args:
            agent_package: Dict from agent-export (format_version 1.0)

        Returns:
            str: Point ID in Qdrant

        Example:
            with open('agent.json') as f:
                package = json.load(f)
            registry.register_agent(package)
        """
        try:
            agent_id = agent_package.get("agent_id", "unknown")
            epistemic = agent_package.get("epistemic_profile", {})
            provenance = agent_package.get("provenance", {})

            # Use postflight vectors if available, else preflight
            vectors = epistemic.get("postflight_vectors") or epistemic.get("preflight_vectors", {})

            # Build 13D vector for Qdrant (fill missing with 0.5)
            vector = [vectors.get(key, 0.5) for key in self.VECTOR_KEYS]

            # Generate point ID
            point_id = self._persona_id_to_point_id(agent_id)

            # Prepare metadata for discovery
            metadata = {
                "persona_id": agent_id,
                "name": f"Epistemic Agent: {agent_id}",
                "version": agent_package.get("format_version", "1.0"),
                "agent_type": "epistemic_agent",
                "persona_type": agent_package.get("persona_id", "general"),
                "focus_domains": [provenance.get("investigation_path", "general")],
                "reputation_score": agent_package.get("reputation_seed", 0.5),
                "merge_score": epistemic.get("merge_score"),
                "learning_delta": epistemic.get("learning_delta", {}),
                "source_project": provenance.get("project_id"),
                "source_session": provenance.get("session_id"),
                "branch_id": agent_package.get("branch_id"),
                "export_timestamp": agent_package.get("export_timestamp"),
                "tags": ["epistemic_agent", "imported", agent_package.get("persona_id", "general")],
            }

            # Create point
            point = PointStruct(id=point_id, vector=vector, payload=metadata)

            # Upsert
            self.client.upsert(collection_name=self.COLLECTION_NAME, points=[point])

            logger.info(f"✓ Registered agent: {agent_id} (point_id={point_id})")
            return str(point_id)

        except Exception as e:
            logger.error(f"Failed to register agent: {e}")
            raise ValueError(f"Cannot register agent: {e}") from e

    def find_agents_by_domain(self, domain: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        Find epistemic agents by investigation domain.

        Args:
            domain: Domain keyword (e.g., "security", "multi-persona")
            limit: Maximum results

        Returns:
            List of agent dicts with metadata
        """
        try:
            results = self.client.scroll(collection_name=self.COLLECTION_NAME, limit=limit * 2)

            matching = []
            for point in results[0]:
                # Check if it's an epistemic agent
                if point.payload.get("agent_type") != "epistemic_agent":
                    continue
                # Check domain match
                focus = point.payload.get("focus_domains", [])
                if any(domain.lower() in d.lower() for d in focus):
                    matching.append(self._point_to_agent_dict(point))

            return matching[:limit]

        except Exception as e:
            logger.warning(f"Error finding agents: {e}")
            return []

    def find_agents_by_reputation(self, min_reputation: float = 0.5, limit: int = 10) -> list[dict[str, Any]]:
        """
        Find high-reputation epistemic agents.

        Args:
            min_reputation: Minimum reputation score
            limit: Maximum results

        Returns:
            List of agents sorted by reputation
        """
        try:
            results = self.client.scroll(collection_name=self.COLLECTION_NAME, limit=1000)

            agents = []
            for point in results[0]:
                if point.payload.get("agent_type") != "epistemic_agent":
                    continue
                rep = point.payload.get("reputation_score", 0.5)
                if rep >= min_reputation:
                    agent = self._point_to_agent_dict(point)
                    agents.append(agent)

            # Sort by reputation
            agents.sort(key=lambda x: x.get("reputation_score", 0), reverse=True)
            return agents[:limit]

        except Exception as e:
            logger.warning(f"Error finding agents by reputation: {e}")
            return []

    @staticmethod
    def _point_to_agent_dict(point: Any) -> dict[str, Any]:
        """Convert Qdrant point to agent dict"""
        return {
            "agent_id": point.payload.get("persona_id"),
            "name": point.payload.get("name"),
            "persona_type": point.payload.get("persona_type"),
            "focus_domains": point.payload.get("focus_domains", []),
            "reputation_score": point.payload.get("reputation_score", 0.5),
            "merge_score": point.payload.get("merge_score"),
            "learning_delta": point.payload.get("learning_delta", {}),
            "source_project": point.payload.get("source_project"),
            "branch_id": point.payload.get("branch_id"),
            "tags": point.payload.get("tags", []),
            "vector": point.vector if hasattr(point, "vector") else None,
        }

    # Helper methods

    @staticmethod
    def _persona_id_to_point_id(persona_id: str) -> int:
        """Convert persona_id to Qdrant point ID"""
        # Use SHA256 hash of persona_id, then take first 8 bytes as int
        hash_val = sha256(persona_id.encode()).digest()
        point_id = int.from_bytes(hash_val[:8], byteorder="big") % (2**31 - 1)
        return point_id

    @staticmethod
    def _point_to_persona_dict(point: Any) -> dict[str, Any]:
        """Convert Qdrant point to persona dict"""
        return {
            "persona_id": point.payload.get("persona_id"),
            "name": point.payload.get("name"),
            "version": point.payload.get("version"),
            "public_key": point.payload.get("public_key"),
            "persona_type": point.payload.get("persona_type"),
            "focus_domains": point.payload.get("focus_domains", []),
            "created_at": point.payload.get("created_at"),
            "reputation_score": point.payload.get("reputation_score", 0.5),
            "tags": point.payload.get("tags", []),
            "vector": point.vector if hasattr(point, "vector") else None,
        }
