"""
Empirica serve-daemon HTTP API.

REST API for querying epistemic state, learning deltas, and git-epistemic
correlations. Served by `empirica serve`, which mounts the FastAPI app in
`empirica.api.serve_app` (routers: artifacts, credentials, entities,
engagements, calibration). New daemon routes are FastAPI routers added
there — never Flask blueprints.

NOTE: this package-level __init__ deliberately stays import-light so sibling
utility modules like `empirica.api.registry` can be imported on installs
without the `[api]` extra (fastapi/uvicorn). Do NOT add eager imports of
serve_app or other extra-dependent modules here.

History: an earlier Flask app (`empirica.api.app` + its blueprint routes)
predated the FastAPI serve daemon and was never served by it — removed as
dead code. The one route that was still needed (calibration) was ported to
a FastAPI router in serve_app first (#230). Guard against regression:
mesh-support prop_flzmft22lz was a `projects-discover --register` crash
(`No module named flask`) caused by this __init__ eagerly importing the
Flask app — the light-import rule above is what prevents it recurring.
"""
