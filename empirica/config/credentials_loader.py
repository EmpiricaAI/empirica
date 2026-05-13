#!/usr/bin/env python3
"""
Centralized Credentials Loader for Empirica AI Adapters

Features:
- Load credentials from YAML config
- Environment variable interpolation
- Fallback to legacy dotfiles
- Caching for performance
- Model validation
"""

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Try to import YAML, fallback to JSON if not available
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    logger.warning("PyYAML not installed, YAML support disabled. Install with: pip install pyyaml")

import json  # noqa: E402 — intentionally after conditional yaml import


class CredentialsLoader:
    """Load and manage AI adapter credentials"""

    # Singleton pattern for caching
    _instance = None
    _credentials_cache = None

    def __new__(cls):
        """Create singleton instance of credentials loader."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize credentials loader and cache."""
        if self._credentials_cache is None:
            self._load_credentials()

    def _find_config_file(self) -> Path | None:
        """
        Find credentials config file in order of precedence:
        1. Environment variable EMPIRICA_CREDENTIALS_PATH
        2. .empirica/credentials.yaml (repo root)
        3. .empirica/credentials.json (repo root)
        4. ~/.empirica/credentials.yaml (home dir)
        """
        # Check environment variable first
        env_path = os.getenv('EMPIRICA_CREDENTIALS_PATH')
        if env_path and Path(env_path).exists():
            return Path(env_path)

        # Repo root .empirica directory
        # Navigate from empirica/config/ to repo root
        repo_root = Path(__file__).parent.parent.parent
        local_config = repo_root / '.empirica'

        if YAML_AVAILABLE and (local_config / 'credentials.yaml').exists():
            return local_config / 'credentials.yaml'
        if (local_config / 'credentials.json').exists():
            return local_config / 'credentials.json'

        # Home directory
        home_config = Path.home() / '.empirica'

        if YAML_AVAILABLE and (home_config / 'credentials.yaml').exists():
            return home_config / 'credentials.yaml'
        if (home_config / 'credentials.json').exists():
            return home_config / 'credentials.json'

        return None

    def _load_credentials(self):
        """Load credentials from config file or fallback to dotfiles"""
        config_file = self._find_config_file()

        if config_file:
            logger.info(f"✅ Loading credentials from: {config_file}")

            try:
                if config_file.suffix in ['.yaml', '.yml']:
                    if not YAML_AVAILABLE:
                        logger.error("YAML config found but PyYAML not installed")
                        self._credentials_cache = self._load_from_dotfiles()
                        return

                    with open(config_file) as f:
                        config = yaml.safe_load(f)
                else:
                    with open(config_file) as f:
                        config = json.load(f)

                # Interpolate environment variables
                self._credentials_cache = self._interpolate_env_vars(config)
                logger.info(f"   Loaded {len(config.get('providers', {}))} provider configurations")

            except Exception as e:
                logger.error(f"Failed to load credentials config: {e}")
                logger.warning("Falling back to legacy dotfiles")
                self._credentials_cache = self._load_from_dotfiles()

        else:
            logger.warning("⚠️ No credentials config found, falling back to legacy dotfiles")
            self._credentials_cache = self._load_from_dotfiles()

    def _interpolate_env_vars(self, config: dict) -> dict:
        """Replace ${VAR_NAME} with environment variable values"""
        def replace_vars(obj: Any) -> Any:
            """Recursively replace env vars in nested structure."""
            if isinstance(obj, dict):
                return {k: replace_vars(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [replace_vars(item) for item in obj]
            elif isinstance(obj, str):
                # Replace ${VAR_NAME} with env var value
                pattern = r'\$\{([A-Z_0-9]+)\}'

                def replacer(match: re.Match) -> str:
                    """Replace single env var match with its value."""
                    var_name = match.group(1)
                    value = os.getenv(var_name)
                    if value is None:
                        logger.debug(f"Environment variable {var_name} not set, using placeholder")
                        return match.group(0)  # Return original if not found
                    return value

                return re.sub(pattern, replacer, obj)
            else:
                return obj

        return replace_vars(config)

    def _load_from_dotfiles(self) -> dict:
        """Fallback: Load from legacy dotfiles"""
        repo_root = Path(__file__).parent.parent.parent

        credentials = {
            'version': '1.0',
            'providers': {},
            'source': 'dotfiles'
        }

        # Map dotfiles to providers
        dotfile_map = {
            'qwen': '.qwen_api',
            'minimax': '.minimax_key',  # Note: user has .minimax_key
            'rovodev': '.rovodev_api',
            'gemini': '.gemini_api',
            'qodo': '.qodo_api',
            'openrouter': '.open_router_api'
        }

        loaded_count = 0
        for provider, dotfile in dotfile_map.items():
            dotfile_path = repo_root / dotfile
            if dotfile_path.exists():
                try:
                    with open(dotfile_path) as f:
                        api_key = f.read().strip()

                    if api_key:
                        credentials['providers'][provider] = {
                            'api_key': api_key,
                            'source': 'dotfile',
                            'dotfile': str(dotfile_path)
                        }
                        loaded_count += 1
                        logger.debug(f"   Loaded {provider} from {dotfile}")
                except Exception as e:
                    logger.warning(f"Failed to load {dotfile}: {e}")

        logger.info(f"   Loaded {loaded_count} API keys from dotfiles")
        return credentials

    def get_cortex_config(self) -> dict[str, str | None]:
        """Return Cortex {url, api_key} resolved by precedence:

        1. Env vars (CORTEX_REMOTE_URL / CORTEX_URL, CORTEX_API_KEY)
        2. `cortex:` block in credentials file (~/.empirica/credentials.yaml)
        3. Empty strings if neither

        The browser extension stores its own copy in chrome.storage
        (`cortexUrl` + `cortexApiKey`); this is the CLI-side equivalent
        so users don't have to export env vars in every shell.

        Returns: {"url": str | None, "api_key": str | None}
        """
        env_url = os.getenv("CORTEX_REMOTE_URL") or os.getenv("CORTEX_URL")
        env_key = os.getenv("CORTEX_API_KEY")
        if env_url and env_key:
            return {"url": env_url.rstrip("/"), "api_key": env_key}

        if not self._credentials_cache:
            self._load_credentials()

        file_cfg = self._credentials_cache.get("cortex") if self._credentials_cache else None
        file_url = file_cfg.get("url") if isinstance(file_cfg, dict) else None
        file_key = file_cfg.get("api_key") if isinstance(file_cfg, dict) else None

        # Env wins per-field — partial-env-override is fine
        url = env_url or file_url
        key = env_key or file_key
        return {
            "url": url.rstrip("/") if url else None,
            "api_key": key or None,
        }

    def get_provider_config(self, provider: str) -> dict[str, Any] | None:
        """
        Get configuration for a specific provider

        Args:
            provider: Provider name (qwen, minimax, etc.)

        Returns:
            Dict with provider config or None if not found
        """
        if not self._credentials_cache:
            self._load_credentials()

        providers = self._credentials_cache.get('providers', {})
        return providers.get(provider)

    def get_api_key(self, provider: str) -> str | None:
        """Get API key for provider"""
        config = self.get_provider_config(provider)
        return config.get('api_key') if config else None

    def get_base_url(self, provider: str) -> str | None:
        """Get base URL for provider"""
        config = self.get_provider_config(provider)
        return config.get('base_url') if config else None

    def get_headers(self, provider: str) -> dict[str, str]:
        """
        Get HTTP headers for provider

        Automatically interpolates ${api_key} in headers
        """
        config = self.get_provider_config(provider)
        if not config:
            return {}

        headers = config.get('headers', {})
        api_key = config.get('api_key', '')

        # Replace ${api_key} in header values
        interpolated_headers = {}
        for key, value in headers.items():
            if isinstance(value, str):
                interpolated_headers[key] = value.replace('${api_key}', api_key)
            else:
                interpolated_headers[key] = value

        return interpolated_headers

    def get_default_model(self, provider: str) -> str | None:
        """Get default model for provider"""
        config = self.get_provider_config(provider)
        return config.get('default_model') if config else None

    def get_available_models(self, provider: str) -> list:
        """Get list of available models for provider"""
        config = self.get_provider_config(provider)
        return config.get('available_models', []) if config else []

    def validate_model(self, provider: str, model: str) -> bool:
        """Check if model is available for provider"""
        available = self.get_available_models(provider)
        if not available:
            return True  # No restrictions if not specified
        return model in available

    def get_auth_method(self, provider: str) -> str:
        """Get authentication method (header, query_param, cli)"""
        config = self.get_provider_config(provider)
        return config.get('auth_method', 'header') if config else 'header'

    def list_providers(self) -> list:
        """List all configured providers"""
        if not self._credentials_cache:
            self._load_credentials()
        return list(self._credentials_cache.get('providers', {}).keys())

    def reload(self):
        """Reload credentials from file"""
        self._credentials_cache = None
        self._load_credentials()


# Global instance
_loader = None


def get_credentials_loader() -> CredentialsLoader:
    """Get global credentials loader instance"""
    global _loader
    if _loader is None:
        _loader = CredentialsLoader()
    return _loader


if __name__ == "__main__":
    # Test credentials loader
    print("=" * 70)
    print("  CREDENTIALS LOADER TEST")
    print("=" * 70)

    loader = get_credentials_loader()

    print(f"\n✅ Credentials source: {loader._credentials_cache.get('source', 'config')}")
    print(f"✅ Providers configured: {len(loader.list_providers())}")

    # Test all providers
    providers = loader.list_providers()

    if not providers:
        print("\n⚠️ No providers configured!")
        print("\nExpected one of:")
        print("  - .empirica/credentials.yaml")
        print("  - Legacy dotfiles (.qwen_api, .minimax_key, etc.)")
    else:
        for provider in providers:
            print(f"\n{provider.upper()}:")
            config = loader.get_provider_config(provider)
            if config:
                has_key = bool(loader.get_api_key(provider))
                print(f"  API Key: {'✅ Configured' if has_key else '❌ Missing'}")

                base_url = loader.get_base_url(provider)
                if base_url:
                    print(f"  Base URL: {base_url}")

                default_model = loader.get_default_model(provider)
                if default_model:
                    print(f"  Default Model: {default_model}")

                models = loader.get_available_models(provider)
                if models:
                    print(f"  Available Models ({len(models)}): {', '.join(models[:3])}{'...' if len(models) > 3 else ''}")

                headers = loader.get_headers(provider)
                if headers:
                    print(f"  Headers: {', '.join(headers.keys())}")

                source = config.get('source')
                if source:
                    print(f"  Source: {source}")

    print("\n" + "=" * 70)
    print("  ✅ TEST COMPLETE")
    print("=" * 70)
