"""Every credentials writer keeps a credentials.json as JSON (1.17.12 sweep found save_ntfy_config; this is its twin).

save_cortex_config had its own yaml.dump copy. auth login calls it whenever the URL came from --url or the hosted default, so
on a machine whose credentials file is JSON the sign-in rewrote it as YAML and every later read failed.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def loader(tmp_path, monkeypatch):
    from empirica.config.credentials_loader import CredentialsLoader

    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"version": "1.0", "providers": {"x": {"k": "v"}}, "cortex": {"api_key": "old"}}))
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(path))
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    yield CredentialsLoader(), path
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None


def test_save_cortex_config_keeps_json_and_the_other_blocks(loader):
    ldr, path = loader
    ldr.save_cortex_config(url="https://cortex.example")
    data = json.loads(path.read_text())  # raised before: the file had become YAML
    # the retired api_key is dropped on write (goal 739e47b2); the file stays JSON with its other blocks
    assert data["cortex"] == {"url": "https://cortex.example"} and data["providers"] == {"x": {"k": "v"}}


def test_no_writer_in_the_loader_dumps_yaml_on_its_own():
    """One writer (_write_credentials) decides the format; a second copy is how the JSON bug came back."""
    from pathlib import Path

    import empirica.config.credentials_loader as cl

    assert Path(cl.__file__).read_text().count("yaml.dump(") == 1
