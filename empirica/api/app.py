"""
Flask application for Empirica Dashboard API

Uses SessionDatabase through the existing abstraction layer.
Backend selection (SQLite/PostgreSQL) is driven by environment:
  - EMPIRICA_DB_TYPE=postgresql + EMPIRICA_DB_HOST/PORT/NAME/USER/PASSWORD
  - Or defaults to SQLite via config.yaml / path_resolver
"""

import logging
import os

from flask import Flask, jsonify

logger = logging.getLogger(__name__)

# Module-level database instance (shared across requests)
_db = None


def get_db():
    """Get or create the shared SessionDatabase instance."""
    global _db
    if _db is None:
        from empirica.data.session_database import SessionDatabase

        db_type = os.environ.get("EMPIRICA_DB_TYPE")
        _db = SessionDatabase(db_type=db_type)
        logger.info(f"Database initialized: dialect={_db.adapter.dialect}")
    return _db


def create_app() -> Flask:
    """Create and configure Flask application"""

    app = Flask(__name__, static_url_path="/api/v1/static", static_folder="./static")

    # CORS configuration
    # Security: In production, set CORS_ORIGIN to your specific frontend domain(s)
    # Example: CORS_ORIGIN=https://your-dashboard.example.com
    # The default "*" is for development only and allows requests from any origin
    allowed_origin = os.environ.get("CORS_ORIGIN", "*")
    if allowed_origin == "*":
        logger.warning("CORS_ORIGIN not set - allowing all origins (development mode)")

    @app.after_request
    def add_cors_headers(response):  # pyright: ignore[reportUnusedFunction]
        """Add CORS headers to all responses."""
        response.headers["Access-Control-Allow-Origin"] = allowed_origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return response

    # Eagerly initialize database (creates tables if needed)
    try:
        db = get_db()
        logger.info(f"API database ready: {db.adapter.dialect}")
    except Exception as e:
        logger.warning(f"Database init deferred: {e}")

    # Health check endpoint
    @app.route("/health", methods=["GET"])
    def health_check():  # pyright: ignore[reportUnusedFunction]
        """Return API health status."""
        try:
            db = get_db()
            dialect = db.adapter.dialect
        except Exception:
            dialect = "unavailable"
        return jsonify({"status": "ok", "service": "empirica-api", "backend": dialect})

    # Register blueprints
    from .auth import APIKeyMiddleware
    from .routes import comparison, deltas, heatmaps, project, sessions, verification

    # Apply API key authentication middleware
    app.wsgi_app = APIKeyMiddleware(app.wsgi_app, prefix="/api/v1")  # type: ignore[method-assign]

    app.register_blueprint(sessions.bp, url_prefix="/api/v1")
    # NOTE: calibration is deliberately NOT registered here — it moved to a
    # FastAPI router in serve_app.py (the app `empirica serve` actually runs).
    # Registering it on this Flask app left it unserved by the daemon (404 on
    # GET /api/v1/calibration/config — the extension's "Sentinel Config" tab).
    app.register_blueprint(deltas.bp, url_prefix="/api/v1")
    app.register_blueprint(verification.bp, url_prefix="/api/v1")
    app.register_blueprint(heatmaps.bp, url_prefix="/api/v1")
    app.register_blueprint(comparison.bp, url_prefix="/api/v1")
    app.register_blueprint(project.bp, url_prefix="/api/v1")

    # Global error handler
    @app.errorhandler(Exception)
    def handle_error(error):  # pyright: ignore[reportUnusedFunction]
        """Handle uncaught exceptions with JSON error response.

        Security: Only expose detailed error messages in debug mode.
        In production, return generic message to prevent information disclosure.
        """
        logger.error(f"API error: {error}", exc_info=True)

        # Only expose error details in debug/development mode
        is_debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
        error_message = str(error) if is_debug else "An internal error occurred"

        return jsonify(
            {"ok": False, "error": "internal_server_error", "message": error_message, "status_code": 500}
        ), 500

    logger.info("Empirica Dashboard API initialized")
    return app


if __name__ == "__main__":
    app = create_app()
    # Security: Use environment variable for debug mode, default to False
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    app.run(host=host, port=8000, debug=debug_mode)
