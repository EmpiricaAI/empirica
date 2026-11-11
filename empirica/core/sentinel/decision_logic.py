"""
Decision Logic - Persona Selection for Sentinel Orchestration

Analyzes tasks to extract domain signals, queries Qdrant for matching personas,
and returns ranked recommendations with confidence scores.

Usage:
    logic = DecisionLogic()

    # Select personas for a task
    matches = logic.select_personas(
        task="Review authentication implementation for security vulnerabilities",
        max_personas=3
    )

    for match in matches:
        print(f"{match.persona_id}: {match.score:.2f} - {match.rationale}")
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Domain signal patterns for task analysis
DOMAIN_PATTERNS = {
    "security": [
        r"secur\w*",
        r"vulnerab\w*",
        r"auth\w*",
        r"encrypt\w*",
        r"attack\w*",
        r"threat\w*",
        r"injection",
        r"xss",
        r"csrf",
        r"privilege",
        r"permission",
        r"credential",
        r"password",
        r"token",
        r"jwt",
        r"oauth",
        r"saml",
        r"cert\w*",
    ],
    "performance": [
        r"perform\w*",
        r"optim\w*",
        r"speed",
        r"latenc\w*",
        r"throughput",
        r"cache",
        r"memory",
        r"cpu",
        r"profil\w*",
        r"benchmark",
        r"scale",
        r"load",
        r"bottleneck",
    ],
    "architecture": [
        r"architect\w*",
        r"design",
        r"pattern",
        r"structur\w*",
        r"modulari\w*",
        r"coupling",
        r"cohesion",
        r"layer\w*",
        r"service",
        r"microservice",
        r"monolith",
        r"api",
    ],
    "testing": [
        r"test\w*",
        r"unit",
        r"integration",
        r"e2e",
        r"coverage",
        r"mock",
        r"stub",
        r"assert",
        r"expect",
        r"spec",
    ],
    "documentation": [r"doc\w*", r"comment", r"readme", r"explain", r"descri\w*", r"tutorial", r"guide", r"example"],
    "data": [r"data\w*", r"database", r"sql", r"query", r"schema", r"model", r"orm", r"migration", r"index"],
    "infrastructure": [
        r"infra\w*",
        r"deploy\w*",
        r"ci",
        r"cd",
        r"docker",
        r"kubernetes",
        r"k8s",
        r"terraform",
        r"aws",
        r"cloud",
    ],
    "frontend": [r"frontend", r"ui", r"ux", r"react", r"vue", r"angular", r"css", r"html", r"component", r"render"],
    "backend": [r"backend", r"server", r"api", r"endpoint", r"route", r"controller", r"middleware", r"handler"],
}


@dataclass
class DomainSignal:
    """A detected domain signal from task analysis"""

    domain: str
    confidence: float
    matched_terms: list[str] = field(default_factory=list)


@dataclass
class PersonaMatch:
    """A persona match with scoring and rationale"""

    persona_id: str
    name: str
    score: float  # 0.0 - 1.0 combined score
    rationale: str
    domain_relevance: float  # How well domains match
    epistemic_fit: float  # How well epistemic profile fits
    focus_domains: list[str] = field(default_factory=list)
    priors: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert persona recommendation to dictionary representation."""
        return {
            "persona_id": self.persona_id,
            "name": self.name,
            "score": self.score,
            "rationale": self.rationale,
            "domain_relevance": self.domain_relevance,
            "epistemic_fit": self.epistemic_fit,
            "focus_domains": self.focus_domains,
            "priors": self.priors,
        }


class DecisionLogic:
    """
    Decision logic for persona selection in Sentinel orchestration.

    Analyzes tasks to extract domain signals, queries Qdrant for
    matching personas, and returns ranked recommendations.

    Attributes:
        registry: PersonaRegistry instance for Qdrant queries
        min_confidence: Minimum confidence to include a match
        domain_weight: Weight for domain relevance in scoring
        epistemic_weight: Weight for epistemic fit in scoring
    """

    def __init__(
        self,
        qdrant_host: str | None = None,
        qdrant_port: int = 6333,
        min_confidence: float = 0.3,
        domain_weight: float = 0.6,
        epistemic_weight: float = 0.4,
    ):
        """
        Initialize DecisionLogic.

        Args:
            qdrant_host: Qdrant server host (default: the store empirica resolves)
            qdrant_port: Qdrant server port
            min_confidence: Minimum confidence threshold for matches
            domain_weight: Weight for domain relevance (0-1)
            epistemic_weight: Weight for epistemic fit (0-1)
        """
        self.qdrant_host = qdrant_host
        self.qdrant_port = qdrant_port
        self.min_confidence = min_confidence
        self.domain_weight = domain_weight
        self.epistemic_weight = epistemic_weight
        self._registry = None

    @property
    def registry(self):
        """Lazy load PersonaRegistry (only when needed)"""
        if self._registry is None:
            try:
                from empirica.core.qdrant.persona_registry import PersonaRegistry

                self._registry = PersonaRegistry(qdrant_host=self.qdrant_host, qdrant_port=self.qdrant_port)
            except Exception as e:
                logger.warning(f"Could not connect to Qdrant: {e}")
                self._registry = None
        return self._registry

    def analyze_task(self, task: str) -> list[DomainSignal]:
        """
        Analyze a task description to extract domain signals.

        Args:
            task: Task description text

        Returns:
            List of DomainSignal objects, sorted by confidence

        Example:
            signals = logic.analyze_task(
                "Review authentication code for SQL injection vulnerabilities"
            )
            # Returns: [DomainSignal(domain="security", confidence=0.9, ...), ...]
        """
        task_lower = task.lower()
        signals = []

        for domain, patterns in DOMAIN_PATTERNS.items():
            matched_terms = []
            for pattern in patterns:
                matches = re.findall(pattern, task_lower)
                matched_terms.extend(matches)

            if matched_terms:
                # Calculate confidence based on number of unique matches
                unique_matches = list(set(matched_terms))
                # More matches = higher confidence, capped at 1.0
                confidence = min(1.0, len(unique_matches) * 0.25)
                signals.append(DomainSignal(domain=domain, confidence=confidence, matched_terms=unique_matches))

        # Sort by confidence
        signals.sort(key=lambda s: s.confidence, reverse=True)

        logger.debug(f"Task analysis: {len(signals)} domain signals detected")
        return signals

    def select_personas(
        self,
        task: str,
        max_personas: int = 3,
        required_domains: list[str] | None = None,
        excluded_personas: list[str] | None = None,
        epistemic_requirements: dict[str, float] | None = None,
    ) -> list[PersonaMatch]:
        """
        Select best personas for a task.

        Analyzes the task, queries Qdrant for matching personas,
        and returns ranked recommendations.

        Args:
            task: Task description
            max_personas: Maximum personas to return (1-5)
            required_domains: Domains that must be covered
            excluded_personas: Persona IDs to exclude
            epistemic_requirements: Minimum epistemic vector requirements
                e.g., {"know": 0.7, "uncertainty": 0.3}

        Returns:
            List of PersonaMatch objects, sorted by score

        Example:
            matches = logic.select_personas(
                task="Analyze OAuth implementation for security issues",
                max_personas=2,
                required_domains=["security"],
                epistemic_requirements={"know": 0.8}
            )
        """
        excluded_personas = excluded_personas or []
        required_domains = required_domains or []

        # Analyze task for domain signals
        signals = self.analyze_task(task)

        if not signals:
            logger.info("No domain signals detected, using general persona")
            return [self._create_general_match()]

        # Get top domains from signals
        top_domains = [s.domain for s in signals[:3]]

        # Add required domains
        for domain in required_domains:
            if domain not in top_domains:
                top_domains.append(domain)

        # Query Qdrant for matching personas
        matches = []

        if self.registry:
            for domain in top_domains:
                try:
                    personas = self.registry.find_personas_by_domain(domain=domain, limit=max_personas * 2)

                    for persona in personas:
                        persona_id = persona.get("persona_id", "")

                        # Skip excluded
                        if persona_id in excluded_personas:
                            continue

                        # Skip if already matched
                        if any(m.persona_id == persona_id for m in matches):
                            continue

                        # Check epistemic requirements
                        if not self._meets_epistemic_requirements(persona, epistemic_requirements):
                            continue

                        # Calculate match score
                        match = self._score_persona(persona, signals, domain)
                        if match.score >= self.min_confidence:
                            matches.append(match)

                except Exception as e:
                    logger.warning(f"Error querying domain {domain}: {e}")

        # If no matches from Qdrant, use signal-based fallback
        if not matches:
            logger.info("No Qdrant matches, using signal-based fallback")
            for signal in signals[:max_personas]:
                matches.append(
                    PersonaMatch(
                        persona_id=f"{signal.domain}_expert",
                        name=f"{signal.domain.title()} Expert",
                        score=signal.confidence * 0.7,  # Discount for fallback
                        rationale=f"Domain signal match: {', '.join(signal.matched_terms)}",
                        domain_relevance=signal.confidence,
                        epistemic_fit=0.5,  # Unknown
                        focus_domains=[signal.domain],
                    )
                )

        # Sort by score and limit
        matches.sort(key=lambda m: m.score, reverse=True)
        result = matches[:max_personas]

        logger.info(f"Selected {len(result)} personas for task: {[m.persona_id for m in result]}")

        return result

    def _score_persona(self, persona: dict[str, Any], signals: list[DomainSignal], primary_domain: str) -> PersonaMatch:
        """Score a persona against task signals"""
        persona_id = persona.get("persona_id", "unknown")
        name = persona.get("name", persona_id)
        focus_domains = persona.get("focus_domains", [])

        # Domain relevance: how many task domains match persona domains
        signal_domains = {s.domain for s in signals}
        matching_domains = signal_domains.intersection(set(focus_domains))
        domain_relevance = len(matching_domains) / max(len(signal_domains), 1)

        # Boost for primary domain match
        if primary_domain in focus_domains:
            domain_relevance = min(1.0, domain_relevance + 0.3)

        # Epistemic fit: use reputation and uncertainty from persona
        reputation = persona.get("reputation_score", 0.5)
        # Higher reputation = better fit
        epistemic_fit = reputation

        # Combined score
        score = self.domain_weight * domain_relevance + self.epistemic_weight * epistemic_fit

        # Build rationale
        domain_matches = ", ".join(matching_domains) if matching_domains else "general"
        rationale = f"Domain match: {domain_matches} (relevance={domain_relevance:.2f}, reputation={reputation:.2f})"

        return PersonaMatch(
            persona_id=persona_id,
            name=name,
            score=score,
            rationale=rationale,
            domain_relevance=domain_relevance,
            epistemic_fit=epistemic_fit,
            focus_domains=focus_domains,
            priors={},  # Could extract from vector if needed
        )

    def _meets_epistemic_requirements(self, persona: dict[str, Any], requirements: dict[str, float] | None) -> bool:
        """Check if persona meets epistemic requirements"""
        if not requirements:
            return True

        vector = persona.get("vector", [])
        if not vector or len(vector) < 13:
            return True  # Can't verify, assume OK

        # Map vector to keys
        from empirica.core.qdrant.persona_registry import PersonaRegistry

        vector_dict = dict(zip(PersonaRegistry.VECTOR_KEYS, vector))

        for key, min_value in requirements.items():
            actual = vector_dict.get(key, 0.5)
            if actual < min_value:
                return False

        return True

    def _create_general_match(self) -> PersonaMatch:
        """Create a general/fallback persona match"""
        return PersonaMatch(
            persona_id="general",
            name="General Expert",
            score=0.5,
            rationale="No specific domain signals detected",
            domain_relevance=0.5,
            epistemic_fit=0.5,
            focus_domains=["general"],
        )

    def get_domain_coverage(self, personas: list[PersonaMatch]) -> dict[str, list[str]]:
        """
        Analyze domain coverage of selected personas.

        Args:
            personas: List of PersonaMatch objects

        Returns:
            Dict mapping domains to persona IDs that cover them
        """
        coverage = {}
        for persona in personas:
            for domain in persona.focus_domains:
                if domain not in coverage:
                    coverage[domain] = []
                coverage[domain].append(persona.persona_id)

        return coverage

    def suggest_additional_personas(
        self, task: str, current_personas: list[PersonaMatch], uncovered_domains: list[str]
    ) -> list[PersonaMatch]:
        """
        Suggest additional personas to cover uncovered domains.

        Args:
            task: Original task
            current_personas: Already selected personas
            uncovered_domains: Domains not yet covered

        Returns:
            Additional persona suggestions
        """
        excluded = [p.persona_id for p in current_personas]

        return self.select_personas(
            task=task,
            max_personas=len(uncovered_domains),
            required_domains=uncovered_domains,
            excluded_personas=excluded,
        )
