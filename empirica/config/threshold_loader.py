#!/usr/bin/env python3
"""
Dynamic Threshold Configuration Loader

Part of the Empirica Meta-Agent Configuration Object (MCO) architecture.
Loads threshold profiles from YAML and provides runtime configuration.

Architecture:
    - MCO Components: personas, cascade_styles (this), protocols, models
    - This loader manages CASCADE style thresholds (Theta in EP framework)
    - Sentinel uses this to program AI cognitive flow control

Usage:
    # Get singleton instance
    loader = ThresholdLoader.get_instance()

    # Load profile
    loader.load_profile('exploratory')

    # Get threshold value
    engagement = loader.get('engagement_threshold')  # Returns: 0.50
    max_rounds = loader.get('cascade.max_investigation_rounds')  # Returns: 10

    # Override specific threshold
    loader.override('uncertainty.high', 0.65)

    # For Sentinel: Create custom profile
    loader.create_custom_profile('task-123', base='default', overrides={
        'uncertainty.high': 0.60,
        'cascade.max_investigation_rounds': 5
    })
"""

import copy
import logging
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


class ThresholdLoader:
    """
    Dynamic threshold configuration loader with singleton pattern.

    Loads threshold profiles from cascade_styles.yaml and provides:
    - Profile switching (default, exploratory, rigorous, rapid, expert, novice)
    - Runtime overrides for specific thresholds
    - Custom profile creation for Sentinel orchestration
    - Fallback to hardcoded defaults if YAML fails
    """

    _instance: Optional["ThresholdLoader"] = None
    _initialized: bool = False

    def __init__(self, config_path: Path | None = None):
        """
        Initialize threshold loader.

        Args:
            config_path: Optional path to cascade_styles.yaml (defaults to mco/cascade_styles.yaml)

        Note: Use get_instance() instead of direct instantiation for singleton pattern
        """
        if config_path is None:
            config_path = Path(__file__).parent / "mco" / "cascade_styles.yaml"

        self.config_path = config_path
        self.profiles: dict[str, dict[str, Any]] = {}
        self.current_profile_name: str = "default"
        self.current_profile: dict[str, Any] = {}
        self.overrides: dict[str, Any] = {}
        self.metadata: dict[str, Any] = {}

        # Load profiles from YAML
        self._load_profiles()

    @classmethod
    def get_instance(cls, config_path: Path | None = None) -> "ThresholdLoader":
        """
        Get singleton instance of ThresholdLoader.

        Args:
            config_path: Optional path to cascade_styles.yaml (only used on first call)

        Returns:
            Singleton ThresholdLoader instance
        """
        if cls._instance is None:
            cls._instance = cls(config_path)
            cls._initialized = True
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset singleton instance (for testing)"""
        cls._instance = None
        cls._initialized = False

    def _load_profiles(self):
        """Load all profiles from YAML configuration file"""
        try:
            if not self.config_path.exists():
                logger.warning(f"Threshold config not found: {self.config_path}")
                self._load_hardcoded_defaults()
                return

            with open(self.config_path) as f:
                data = yaml.safe_load(f)

                if not data or "profiles" not in data:
                    logger.error("Invalid threshold config: missing 'profiles' key")
                    self._load_hardcoded_defaults()
                    return

                self.profiles = data.get("profiles", {})
                self.metadata = data.get("metadata", {})

                # Set default profile
                default_profile_name = self.metadata.get("default_profile", "default")
                if default_profile_name in self.profiles:
                    self.current_profile_name = default_profile_name
                    self.current_profile = self.profiles[default_profile_name]
                else:
                    logger.warning(f"Default profile '{default_profile_name}' not found, using 'default'")
                    self.current_profile_name = "default"
                    self.current_profile = self.profiles.get("default", {})

                logger.info(f"✅ Loaded {len(self.profiles)} threshold profiles from {self.config_path}")
                logger.info(f"   Active profile: {self.current_profile_name}")

        except Exception as e:
            logger.error(f"Failed to load threshold config: {e}")
            self._load_hardcoded_defaults()

    def _load_hardcoded_defaults(self):
        """
        Fallback to hardcoded defaults if YAML loading fails.

        This ensures backwards compatibility - if cascade_styles.yaml is missing,
        we fall back to the original hardcoded values from thresholds.py.
        """
        logger.warning("⚠️  Using hardcoded threshold defaults (YAML config unavailable)")

        try:
            from empirica.core import thresholds

            # Build default profile from hardcoded constants
            self.profiles["default"] = {
                "name": "Default (Hardcoded)",
                "description": "Fallback to original hardcoded thresholds",
                "engagement_threshold": thresholds.ENGAGEMENT_THRESHOLD,
                "critical": thresholds.CRITICAL_THRESHOLDS,
                "uncertainty": {
                    "low": thresholds.UNCERTAINTY_LOW,
                    "moderate": thresholds.UNCERTAINTY_MODERATE,
                },
                "comprehension": {
                    "high": thresholds.COMPREHENSION_HIGH,
                    "moderate": thresholds.COMPREHENSION_MODERATE,
                    "clarity_min": thresholds.CLARITY_THRESHOLD,
                    "signal_min": thresholds.SIGNAL_THRESHOLD,
                    "coherence_min": thresholds.COHERENCE_THRESHOLD,
                },
                "execution": {
                    "high": thresholds.EXECUTION_HIGH,
                    "moderate": thresholds.EXECUTION_MODERATE,
                    "state_mapping_min": thresholds.STATE_MAPPING_THRESHOLD,
                    "completion_min": thresholds.COMPLETION_THRESHOLD,
                    "impact_min": thresholds.IMPACT_THRESHOLD,
                },
                "confidence": {
                    "high": thresholds.CONFIDENCE_HIGH,
                    "moderate": thresholds.CONFIDENCE_MODERATE,
                    "low": thresholds.CONFIDENCE_LOW,
                    "goal_orchestrator": thresholds.GOAL_CONFIDENCE_THRESHOLD,
                },
                "cascade": {
                    "max_investigation_rounds": 7,
                    "check_confidence_to_proceed": 0.70,
                },
            }

            self.current_profile_name = "default"
            self.current_profile = self.profiles["default"]

            logger.info("✅ Hardcoded defaults loaded successfully")

        except ImportError as e:
            logger.error(f"Cannot load hardcoded defaults: {e}")
            # Absolute fallback - minimal hardcoded values
            self.profiles["default"] = {
                "engagement_threshold": 0.60,
                "critical": {"coherence_min": 0.50, "density_max": 0.90, "change_min": 0.50},
                "uncertainty": {"low": 0.70, "moderate": 0.30, "high": 0.70},
                "confidence": {"high": 0.85, "moderate": 0.70, "low": 0.50},
                "cascade": {"max_investigation_rounds": 7, "check_confidence_to_proceed": 0.70},
            }
            self.current_profile = self.profiles["default"]

    def load_profile(self, profile_name: str) -> bool:
        """
        Load a specific profile by name.

        Args:
            profile_name: Name of profile (default, exploratory, rigorous, rapid, expert, novice)

        Returns:
            True if profile loaded successfully, False otherwise
        """
        if profile_name not in self.profiles:
            logger.error(f"❌ Profile '{profile_name}' not found. Available: {list(self.profiles.keys())}")
            return False

        self.current_profile_name = profile_name
        self.current_profile = self.profiles[profile_name]
        self.overrides = {}  # Clear overrides when switching profiles

        logger.info(f"✅ Loaded threshold profile: {profile_name}")
        logger.info(f"   Description: {self.current_profile.get('description', 'N/A')}")

        return True

    @staticmethod
    def select_profile_for_work(
        work_type: str | None = None,
        work_context: str | None = None,
    ) -> str:
        """Select the best cascade_styles profile for the given work parameters.

        Maps PREFLIGHT work_type/work_context to one of the defined profiles
        (default, exploratory, rigorous, rapid, expert, novice).

        Priority: work_context takes precedence (it describes the working mode),
        work_type refines within that (what kind of work).

        Args:
            work_type: Type of work (code, research, debug, release, audit, etc.)
            work_context: Working mode (greenfield, iteration, investigation, refactor)

        Returns:
            Profile name string.
        """
        # work_context → profile (primary signal)
        context_map = {
            "greenfield": "exploratory",
            "investigation": "exploratory",
            "refactor": "default",
            "iteration": "default",
        }

        # work_type overrides within iteration context
        type_overrides = {
            "research": "exploratory",
            "audit": "rigorous",
            "release": "rigorous",
            "design": "exploratory",
        }

        # Start with context-based selection
        profile = context_map.get(work_context, "default")

        # work_type can override for specific combinations
        if work_type in type_overrides:
            type_profile = type_overrides[work_type]
            # Only override if context didn't already set a stronger profile
            # e.g., investigation + audit → keep exploratory (investigation wins)
            if profile == "default":
                profile = type_profile

        return profile

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Get threshold value by dot-notation path.

        Args:
            key_path: Dot-separated path (e.g., 'engagement_threshold', 'critical.coherence_min')
            default: Default value if key not found

        Returns:
            Threshold value or default

        Examples:
            >>> loader.get('engagement_threshold')
            0.60
            >>> loader.get('critical.coherence_min')
            0.50
            >>> loader.get('uncertainty.high')
            0.70
            >>> loader.get('cascade.max_investigation_rounds')
            7
        """
        # Check overrides first (highest priority)
        if key_path in self.overrides:
            return self.overrides[key_path]

        # Navigate nested dictionary
        keys = key_path.split(".")
        value = self.current_profile

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    def override(self, key_path: str, value: Any):
        """
        Override a specific threshold value for current session.

        Args:
            key_path: Dot-separated path to threshold
            value: New value to set

        Note: Overrides are cleared when switching profiles

        Examples:
            >>> loader.override('uncertainty.high', 0.65)
            >>> loader.override('cascade.max_investigation_rounds', 5)
        """
        self.overrides[key_path] = value
        logger.info(f"🔧 Threshold override: {key_path} = {value}")

    def clear_overrides(self):
        """Clear all threshold overrides"""
        if self.overrides:
            logger.info(f"🔧 Cleared {len(self.overrides)} threshold overrides")
            self.overrides = {}

    def create_custom_profile(self, name: str, base: str = "default", overrides: dict[str, Any] | None = None) -> bool:
        """
        Create a custom profile based on existing profile with overrides.

        This is the primary method for Sentinel to create task-specific or
        AI-specific threshold profiles.

        Args:
            name: Name for custom profile
            base: Base profile to copy from
            overrides: Dictionary of threshold overrides

        Returns:
            True if profile created successfully, False otherwise

        Example (Sentinel usage):
            >>> # Sentinel analyzes task: "Explore new API patterns"
            >>> loader.create_custom_profile(
            ...     name='task-explore-api-123',
            ...     base='exploratory',
            ...     overrides={
            ...         'cascade.max_investigation_rounds': 12,
            ...         'uncertainty.high': 0.65
            ...     }
            ... )
        """
        if base not in self.profiles:
            logger.error(f"❌ Base profile '{base}' not found")
            return False

        # Deep copy base profile
        custom = copy.deepcopy(self.profiles[base])
        custom["name"] = name
        custom["description"] = f"Custom profile based on {base}"

        # Apply overrides
        if overrides:
            for key_path, value in overrides.items():
                self._set_nested_value(custom, key_path, value)

        # Save custom profile
        self.profiles[name] = custom
        logger.info(f"✅ Created custom profile: {name} (base: {base}, {len(overrides or {})} overrides)")

        return True

    def _set_nested_value(self, d: dict, key_path: str, value: Any):
        """Set value in nested dictionary using dot notation"""
        keys = key_path.split(".")
        for key in keys[:-1]:
            d = d.setdefault(key, {})
        d[keys[-1]] = value

    def get_profile_info(self) -> dict[str, Any]:
        """
        Get current profile metadata.

        Returns:
            Dictionary with profile name, description, and active overrides
        """
        return {
            "name": self.current_profile_name,
            "description": self.current_profile.get("description", ""),
            "overrides": self.overrides,
            "override_count": len(self.overrides),
        }

    def list_profiles(self) -> dict[str, str]:
        """
        List all available profiles with descriptions.

        Returns:
            Dictionary mapping profile names to descriptions
        """
        return {name: profile.get("description", "") for name, profile in self.profiles.items()}

    def get_all_thresholds(self) -> dict[str, Any]:
        """
        Get all thresholds from current profile (including overrides).

        Returns:
            Dictionary of all threshold values
        """
        # Start with current profile
        result = copy.deepcopy(self.current_profile)

        # Apply overrides
        for key_path, value in self.overrides.items():
            self._set_nested_value(result, key_path, value)

        return result

    def export_for_handoff(self) -> dict[str, Any]:
        """
        Export threshold configuration for epistemic handoff.

        Returns profile name and overrides (not full profile data) so that
        receiving AI can reload appropriate thresholds for its capabilities.

        Returns:
            Minimal handoff data (profile name + overrides)
        """
        return {"profile_name": self.current_profile_name, "overrides": self.overrides}


# Global instance accessor
def ready_know_bar(loader: Any, default: float = 0.70) -> Any:
    """The know bar of the profile `loader` has loaded.

    Only the `default` profile defines `ready_know_threshold`; every other profile carries the bar under its legacy name,
    `ready_confidence_threshold` (cascade_styles.yaml marks it the deprecated alias), so reading the new name alone fell back to a
    hardcoded value for all of them. One definition, because three readers (CHECK, the dynamic thresholds, the monitor's hooks) had
    each hand-written the lookup and one fix to one of them is how the others stay wrong."""
    know = loader.get("cascade.ready_know_threshold", None)
    if know is None:
        know = loader.get("cascade.ready_confidence_threshold", default)
    return know


def get_threshold_config() -> ThresholdLoader:
    """
    Get global ThresholdLoader instance.

    Returns:
        Singleton ThresholdLoader instance

    Usage:
        >>> from empirica.config.threshold_loader import get_threshold_config
        >>> config = get_threshold_config()
        >>> engagement = config.get('engagement_threshold')
    """
    return ThresholdLoader.get_instance()


# Convenience functions for common operations
def load_profile(profile_name: str) -> bool:
    """Load a threshold profile (convenience function)"""
    return get_threshold_config().load_profile(profile_name)


def get_threshold(key_path: str, default: Any = None) -> Any:
    """Get a threshold value (convenience function)"""
    return get_threshold_config().get(key_path, default)


def override_threshold(key_path: str, value: Any):
    """Override a threshold value (convenience function)"""
    get_threshold_config().override(key_path, value)
