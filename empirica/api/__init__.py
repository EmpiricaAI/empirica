"""
Empirica Dashboard API

REST API for querying epistemic state, learning deltas, and git-epistemic correlations.
Foundation for Forgejo plugin and standalone dashboards.

NOTE: `create_app` lives in `empirica.api.app` and depends on flask. flask
is a SOFT dependency (not declared in pyproject) — needed only for the
`empirica serve` daemon. This package-level __init__ deliberately does NOT
eagerly import flask-using modules so that sibling utility modules like
`empirica.api.registry` (which has no flask requirement) can be imported
on flaskless installs. Import `from empirica.api.app import create_app`
explicitly when the daemon is needed.

Surfaced by mesh-support prop_flzmft22lz: `projects-discover --register`
crashed `No module named flask` on installs without flask because
`_register_discovered_to_registry` did `from empirica.api.registry import
...` which ran this __init__ which used to do `from .app import
create_app` eagerly → flask required just to write the local registry.
"""
