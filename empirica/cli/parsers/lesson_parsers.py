"""Lesson management command parsers for Epistemic Procedural Knowledge."""

import argparse


def add_lesson_parsers(subparsers):
    """Add lesson management command parsers"""

    # lesson-create: Create a new lesson
    #
    # The payload schema is undiscoverable from the CLI otherwise: the flags describe
    # HOW to pass JSON and say nothing about what may be in it, so a first-time author
    # learns the shape by submitting wrong payloads until one is accepted. Measured on
    # a peer seat: five sequential failures, each correct and legible, each surfacing
    # exactly one field — a schema learned by bisection.
    #
    # DERIVED from the same constants the validator enforces, never hand-listed. A
    # hand-written copy is a second source of truth for a schema that already has one,
    # and it rots the first time a field is added by someone who did not know the help
    # text existed.
    from empirica.cli.command_handlers.lesson_commands import KNOWN_LESSON_KEYS, LESSON_ENUMS

    _enums = "\n".join(f"    {field}: {' | '.join(allowed)}" for field, allowed in sorted(LESSON_ENUMS.items()))
    lesson_create = subparsers.add_parser(
        "lesson-create",
        help="Create a new lesson from JSON input",
        epilog=(
            "PERMANENT. A lesson cannot be deleted — `delete-artifacts` refuses the type on "
            "purpose, because a lesson that turns out wrong is SUPERSEDED (`--supersedes <id>`) "
            "rather than erased, which keeps the record of having been wrong. That is a good "
            "property for real lessons and it also means a throwaway probe is permanent: two "
            "practitioners created test rows on the same day without considering it, because "
            "nothing said so before the write.\n\n"
            "Re-using an existing (name, version) REPLACES the stored lesson in place — the "
            "receipt reports `replaced: true` and echoes `stored`, so check both rather than "
            "assuming a create was a create.\n\n"
            "Payload fields (--input / --json / stdin). Anything else is rejected by name:\n"
            f"    {', '.join(sorted(KNOWN_LESSON_KEYS))}\n\n"
            "The body is `description`. `steps` is a list of OBJECTS, each with an `action` "
            "key and an optional `phase` of noetic|praxic — not a list of strings.\n\n"
            "Closed vocabularies (out-of-vocabulary values are rejected, never defaulted):\n"
            f"{_enums}\n\n"
            "`sharing_policy` is the lesson store's own axis and is deliberately NOT the "
            "`visibility` flag used by finding-log and its siblings — a lesson carries "
            "marketplace tiers the artifact layer has no concept of. Nearest equivalents: "
            "local~private, shared~org, public~public.\n\n"
            'Example:\n  empirica lesson-create --json \'{"name": "x", "description": "what it '
            'teaches", "steps": [{"order": 1, "phase": "praxic", "action": "do the thing"}]}\''
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    lesson_create.add_argument("--name", help="Lesson name")
    lesson_create.add_argument("--input", "-i", help='Input JSON file (use "-" for stdin)')
    lesson_create.add_argument("--json", help="Inline JSON data")
    lesson_create.add_argument(
        "--from-global",
        dest="from_global",
        help="INGEST a peer's shared lesson by id from the cross-practice pool. The copy is "
        "attributed to its author permanently and can never be re-published from here.",
    )
    lesson_create.add_argument(
        "--supersedes",
        help="Id of a lesson this one REPLACES — writes a supersedes edge so the older one stops being served. "
        "Distinct from bumping version, which publishes a revision of the same lesson.",
    )
    lesson_create.add_argument("--output", choices=["human", "json"], default="json", help="Output format")

    # lesson-load: Load and display a lesson
    lesson_load = subparsers.add_parser("lesson-load", help="Load and display a lesson")
    lesson_load.add_argument("--id", "--lesson-id", dest="lesson_id", required=True, help="Lesson ID (required)")
    lesson_load.add_argument("--steps-only", action="store_true", help="Only show steps")
    lesson_load.add_argument("--output", choices=["human", "json"], default="json", help="Output format")

    # lesson-list: List all lessons
    lesson_list = subparsers.add_parser("lesson-list", help="List all lessons")
    lesson_list.add_argument("--domain", help="Filter by domain")
    lesson_list.add_argument("--limit", type=int, default=20, help="Maximum results (default: 20)")
    lesson_list.add_argument(
        "--include-superseded",
        action="store_true",
        help="Also return lessons a newer lesson replaced, each marked with superseded_by.",
    )
    lesson_list.add_argument("--output", choices=["human", "json"], default="json", help="Output format")

    # lesson-search: Search for lessons
    lesson_search = subparsers.add_parser("lesson-search", help="Search for lessons by query, vector, or domain")
    lesson_search.add_argument("--query", "-q", help="Semantic search query")
    lesson_search.add_argument("--improves", help="Find lessons that improve this vector (know, do, context, etc.)")
    lesson_search.add_argument("--domain", help="Filter by domain")
    lesson_search.add_argument("--limit", type=int, default=10, help="Maximum results (default: 10)")
    lesson_search.add_argument(
        "--include-superseded",
        action="store_true",
        help="Also return lessons a newer lesson replaced, each marked with superseded_by.",
    )
    lesson_search.add_argument("--output", choices=["human", "json"], default="json", help="Output format")

    # lesson-recommend: Get lesson recommendations based on epistemic state
    lesson_recommend = subparsers.add_parser(
        "lesson-recommend", help="Get lesson recommendations based on epistemic state"
    )
    lesson_recommend.add_argument("--session-id", help="Session ID to load epistemic state from")
    lesson_recommend.add_argument("--know", type=float, help="Current know vector (0-1)")
    lesson_recommend.add_argument("--do", type=float, help="Current do vector (0-1)")
    lesson_recommend.add_argument("--context", type=float, help="Current context vector (0-1)")
    lesson_recommend.add_argument("--uncertainty", type=float, help="Current uncertainty vector (0-1)")
    lesson_recommend.add_argument(
        "--threshold", type=float, default=0.6, help='Threshold for "acceptable" (default: 0.6)'
    )
    lesson_recommend.add_argument("--output", choices=["human", "json"], default="json", help="Output format")

    # lesson-stats: Show lesson storage statistics
    lesson_stats = subparsers.add_parser("lesson-stats", help="Show lesson storage statistics")
    lesson_stats.add_argument("--output", choices=["human", "json"], default="json", help="Output format")

    # lesson-embed: Embed lessons into Qdrant
    lesson_embed = subparsers.add_parser("lesson-embed", help="Embed all lessons into Qdrant for semantic search")
    lesson_embed.add_argument("--force", action="store_true", help="Force re-embed all")
    lesson_embed.add_argument("--output", choices=["human", "json"], default="json", help="Output format")
