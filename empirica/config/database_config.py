#!/usr/bin/env python3
"""
Database Configuration Loader

Loads database configuration from (in priority order):
1. DATABASE_URL (standard postgres://... URL, Docker/Heroku convention)
2. EMPIRICA_DB_TYPE + EMPIRICA_DB_* environment variables
3. config.yaml (database section)
4. Defaults to SQLite

Example config.yaml:
    database:
      type: sqlite  # or postgresql
      sqlite:
        path: ./.empirica/sessions/sessions.db
      postgresql:
        host: localhost
        port: 5432
        database: empirica
        user: empirica
        password: ${POSTGRES_PASSWORD}  # env var substitution
"""

import logging
import os
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def get_database_config() -> dict[str, Any]:
    """
    Get database configuration from environment or config file

    Returns:
        Dictionary with database configuration:
        {
            "type": "sqlite" | "postgresql",
            "sqlite": {...},  # if type=sqlite
            "postgresql": {...}  # if type=postgresql
        }
    """
    # Check DATABASE_URL first (standard Docker/Heroku convention)
    database_url = os.environ.get("DATABASE_URL")
    # `postgres://` is the spelling Heroku and Docker hand out; `postgresql://` is SQLAlchemy's. The docstring promised both.
    if database_url and database_url.startswith(("postgres://", "postgresql")):
        from urllib.parse import urlparse

        parsed = urlparse(database_url)
        return {
            "type": "postgresql",
            "postgresql": {
                "host": parsed.hostname or "localhost",
                "port": parsed.port or 5432,
                "database": (parsed.path or "/empirica").lstrip("/"),
                "user": parsed.username or "empirica",
                "password": parsed.password or "",
            },
        }

    # Check EMPIRICA_DB_TYPE environment variable
    db_type = os.environ.get("EMPIRICA_DB_TYPE", "sqlite")

    if db_type == "postgresql":
        return {
            "type": "postgresql",
            "postgresql": {
                "host": os.environ.get("EMPIRICA_DB_HOST", "localhost"),
                "port": int(os.environ.get("EMPIRICA_DB_PORT", "5432")),
                "database": os.environ.get("EMPIRICA_DB_NAME", "empirica"),
                "user": os.environ.get("EMPIRICA_DB_USER", "empirica"),
                "password": os.environ.get("EMPIRICA_DB_PASSWORD", ""),
            },
        }

    # Try to load from config.yaml
    try:
        from empirica.config.path_resolver import get_git_root

        git_root = get_git_root()
        if git_root:
            config_path = git_root / ".empirica" / "config.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                    if config and "database" in config:
                        db_config = config["database"]
                        # Substitute environment variables in config
                        db_config = _substitute_env_vars(db_config)
                        logger.info(f"📊 Loaded database config from {config_path}")
                        return db_config
    except Exception as e:
        # A user who configured PostgreSQL in config.yaml and got SQLite back deserves to be told. This runs on every database open, so
        # it is a warning, not a failure: a broken config must not take the CLI down.
        logger.warning(
            f"Could not load the database config from .empirica/config.yaml ({type(e).__name__}: {e}); "
            "falling back to SQLite. If PostgreSQL was intended, fix the file."
        )

    # Default to SQLite
    logger.info("📊 Using default SQLite database configuration")
    return {
        "type": "sqlite",
        "sqlite": {
            "path": None  # Will use default from path_resolver
        },
    }


def _substitute_env_vars(config: Any) -> Any:
    """
    Recursively substitute ${VAR} with environment variables

    Example:
        password: ${POSTGRES_PASSWORD} -> password: actual_value
    """
    if isinstance(config, dict):
        return {k: _substitute_env_vars(v) for k, v in config.items()}
    elif isinstance(config, list):
        return [_substitute_env_vars(item) for item in config]
    elif isinstance(config, str) and config.startswith("${") and config.endswith("}"):
        var_name = config[2:-1]
        if var_name not in os.environ:
            logger.warning(
                f"config.yaml references ${{{var_name}}} but the environment variable is not set; "
                "the literal text is being used, which will not be a valid value."
            )
            return config
        return os.environ[var_name]
    else:
        return config
