"""
Lesson Commands - CLI handlers for Empirica Lessons

Commands:
- lesson-create: Create a new lesson from JSON input
- lesson-load: Load and display a lesson
- lesson-list: List all lessons
- lesson-search: Search for lessons
- lesson-replay-start: Start tracking a lesson replay
- lesson-replay-end: End a lesson replay
- lesson-stats: Show lesson storage statistics
"""

import json
import logging
import sys
from argparse import Namespace
from typing import Any

logger = logging.getLogger(__name__)

# The payload contract for `lesson-create`, enforced rather than implied.
#
# The handler used to cherry-pick keys with `.get()`, so anything it did not
# recognise vanished and the call still returned ok:true. A caller passing
# summary/title/context/pattern/anti_pattern/application got an empty lesson and
# a success message. Keeping the accepted set in one named place means the error
# can list it, which is what makes the CLI self-describing — `--help` documents
# --name/--input/--json/--output and nothing about the payload schema, so there
# was no way to get this right from the CLI surface alone.
KNOWN_LESSON_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "version",
        "description",
        "epistemic",
        "steps",
        "domain",
        "tags",
        "suggested_tier",
        "suggested_price",
        "created_by",
        "abstraction_level",
        "sharing_policy",
        "abstract_pattern",
        "origin_practice",
        # The id of a lesson THIS ONE replaces. Distinct from bumping `version`:
        # a revision is the same lesson restated, a supersession says the earlier
        # lesson should stop steering work. Writes a `supersedes` graph edge.
        "supersedes",
    }
)

# Closed vocabularies. Mirrors the Literal[...] annotations on Lesson; an
# out-of-vocabulary value is rejected, never silently defaulted.
LESSON_ENUMS: dict[str, tuple[str, ...]] = {
    "abstraction_level": ("personal", "project", "domain", "cross_org"),
    "sharing_policy": ("private", "project", "org", "public", "licensed"),
}

_PHASE_VALUES: frozenset[str] = frozenset({"noetic", "praxic"})


def _supersession_note(storage, include_superseded: bool) -> dict:
    """How many lessons in the store are retired, and whether they were withheld.

    Reported on every read surface, including when nothing is filtered — a zero
    here means "nothing is retired", which is a different statement from the key
    being absent, and only one of the two is checkable.
    """
    retired = len(storage.superseded_ids())
    return {"superseded_in_store": retired, "superseded_withheld": (not include_superseded) and retired > 0}


def _wire_supersession(storage, new_id: str, supersedes: str | None) -> tuple[bool | None, str | None]:
    """Write the ``supersedes`` edge, or say precisely why it was not written.

    Validated against the store FIRST: an edge to a lesson that does not exist
    suppresses nothing and would report success, which is the shape where a
    practitioner believes the old guidance is retired and it keeps being served.
    Returns ``(written, error)`` and the caller echoes both — never just that the
    lesson itself was created.
    """
    # Normalise HERE rather than trusting the caller. The handler already strips,
    # but a helper whose contract depends on its one caller having done so is a
    # helper that breaks the moment it acquires a second one.
    supersedes = (supersedes or "").strip() or None
    if not supersedes:
        return None, None
    if supersedes == new_id:
        return None, "supersedes: a lesson cannot supersede itself — no edge written"
    if storage.get_lesson(supersedes) is None:
        return None, f"supersedes: no lesson with id {supersedes!r} — no edge written"
    if storage.add_edge(new_id, supersedes, "supersedes"):
        return True, None
    return False, f"supersedes: edge to {supersedes!r} could not be written"


def _ingest_from_global(lesson_id: str) -> tuple[dict | None, str | None]:
    """Pull a peer's shared lesson into this store, attributed and non-republishable.

    ON DEMAND by design. Auto-ingesting everything shared would make the local store
    an unfiltered peer feed; a practice's store should hold what its practitioner
    chose to hold. The pull happens at the moment of relevance — you saw the lesson
    in a cross-practice search and you want to keep it.

    **This is the one artifact type that may cross a practice boundary.** Findings,
    unknowns, dead-ends and mistakes are local epistemic state and stay home. A
    lesson is the transfer unit by definition: if a peer can pick it up and act on
    it, it is a lesson. The copy carries `origin_practice` permanently, which is
    what stops it re-entering the pool under our name.

    Refuses rather than approximates: a point published before the record-carrying
    payload has a name and a description and no steps, and minting a plausible stub
    from that is worse than saying no.
    """
    from empirica.core.qdrant.global_sync import fetch_global_lesson

    fetched = fetch_global_lesson(lesson_id)
    if not fetched:
        return None, (
            f"--from-global {lesson_id!r}: no shared lesson with a full record. Either it is not in "
            "the pool, or it was published before records were carried — in which case the peer can "
            "re-publish it. Refusing to reconstruct a lesson from its description alone."
        )

    # Filter to the AUTHORING shape before handing this to the create path.
    #
    # Without this, --from-global has never worked for any lesson that was ever
    # stored: the pool returns the STORED record (id, created_timestamp, org_id,
    # user_id, relations, execution_count …) and the create validator accepts only
    # the fields an author may set, so ingest died on
    # `Unknown field(s): corrections, created_timestamp, entity_ids, …`.
    #
    # A producer/consumer mismatch, and the failure mode is the giveaway: it
    # refused LOUDLY and named twenty fields, which reads like a malformed lesson
    # rather than a broken verb — so the natural response is to blame the peer's
    # record instead of the ingester.
    #
    # Filtered against KNOWN_LESSON_KEYS rather than a hand-list of fields to drop:
    # a drop-list needs editing every time the stored shape grows a column, and
    # would silently start failing again on the first one nobody remembered.
    record = {k: v for k, v in dict(fetched["record"]).items() if k in KNOWN_LESSON_KEYS}
    origin = fetched.get("origin_project_id") or "unknown-practice"
    record["origin_practice"] = origin
    # Ingested at the policy the practitioner chooses later; never inherited as
    # shared, because re-sharing is precisely what must not happen by default.
    record["sharing_policy"] = "private"
    return record, None


def handle_lesson_create_command(args: Namespace) -> dict[str, Any]:
    """
    Create a new lesson from JSON input.

    Usage:
        empirica lesson-create --name "My Lesson" --input lesson.json
        cat lesson.json | empirica lesson-create -

    JSON format:
    {
        "name": "Lesson Name",
        "version": "1.0",
        "description": "What this lesson teaches",
        "epistemic": {
            "source_confidence": 0.9,
            "teaching_quality": 0.85,
            "reproducibility": 0.8,
            "expected_delta": {"know": 0.3, "do": 0.2, "uncertainty": -0.25}
        },
        "steps": [
            {"order": 1, "phase": "noetic", "action": "Read docs"},
            {"order": 2, "phase": "praxic", "action": "Execute", "critical": true}
        ],
        "domain": "example",
        "tags": ["tag1", "tag2"]
    }
    """
    from empirica.core.lessons import (
        EpistemicDelta,
        Lesson,
        LessonEpistemic,
        LessonPhase,
        LessonStep,
        get_lesson_storage,
    )

    getattr(args, "output", "json")

    try:
        # Get input data
        input_data = None

        from_global = getattr(args, "from_global", None)
        if from_global:
            input_data, ingest_error = _ingest_from_global(str(from_global).strip())
            if ingest_error:
                return {"ok": False, "error": ingest_error}
        # From stdin
        elif getattr(args, "input", None) == "-":
            input_data = json.load(sys.stdin)
        # From file
        elif getattr(args, "input", None):
            with open(args.input) as f:
                input_data = json.load(f)
        # From inline JSON
        elif getattr(args, "json", None):
            input_data = json.loads(args.json)
        else:
            return {
                "ok": False,
                "error": "No input provided. Use --input FILE, --json JSON, --from-global ID, or pipe to stdin",
            }

        # Build lesson object
        name = input_data.get("name", getattr(args, "name", "Unnamed Lesson"))
        version = input_data.get("version", "1.0")

        # Parse epistemic data
        epistemic_data = input_data.get("epistemic", {})
        delta_data = epistemic_data.get("expected_delta", {})
        expected_delta = EpistemicDelta(
            know=delta_data.get("know", 0),
            do=delta_data.get("do", 0),
            context=delta_data.get("context", 0),
            clarity=delta_data.get("clarity", 0),
            coherence=delta_data.get("coherence", 0),
            signal=delta_data.get("signal", 0),
            uncertainty=delta_data.get("uncertainty", 0),
        )

        epistemic = LessonEpistemic(
            source_confidence=epistemic_data.get("source_confidence", 0.8),
            teaching_quality=epistemic_data.get("teaching_quality", 0.8),
            reproducibility=epistemic_data.get("reproducibility", 0.7),
            expected_delta=expected_delta,
        )

        # Reject what we cannot store, rather than dropping it and reporting
        # success. A caller passing `summary` plainly intends content; silently
        # discarding it is the worst available behaviour, because the receipt
        # says the lesson was created and nothing says it is empty.
        unknown = sorted(set(input_data) - KNOWN_LESSON_KEYS)
        if unknown:
            return {
                "ok": False,
                "error": (f"Unknown field(s): {', '.join(unknown)}. Accepted: {', '.join(sorted(KNOWN_LESSON_KEYS))}."),
                "unknown_fields": unknown,
                "accepted_fields": sorted(KNOWN_LESSON_KEYS),
            }

        # Enums are REJECTED, not coerced. sharing_policy silently falling back
        # to `private` is the consequential one: it decides whether the lesson
        # crosses the practice boundary at all, which is the entire distinction
        # between a lesson and a finding. A practitioner authoring a lesson to
        # propagate a pattern got a success message and an artifact no peer
        # would ever see.
        for field, allowed in LESSON_ENUMS.items():
            if field in input_data and input_data[field] not in allowed:
                return {
                    "ok": False,
                    "error": (f"Invalid {field}: {input_data[field]!r}. Allowed: {', '.join(allowed)}."),
                }

        # Parse steps
        steps = []
        for idx, step_data in enumerate(input_data.get("steps", [])):
            # A plain-string step crashed with AttributeError at the .get below
            # instead of the clean message every other malformed field gets —
            # this path predates the unknown-field/enum hardening (mesh-support,
            # prop_kdi4qrcc). Same contract: name the step, name the shape.
            if not isinstance(step_data, dict):
                return {
                    "ok": False,
                    "error": (
                        f"Invalid step {idx + 1}: expected an object with an 'action' field "
                        f"(got {type(step_data).__name__}: {step_data!r})."
                    ),
                }
            phase_str = str(step_data.get("phase", "praxic")).lower()
            # Previously: NOETIC if phase_str == "noetic" else PRAXIC — so every
            # unrecognised phase silently became praxic. A six-step lesson using
            # diagnose/remediate/verify stored six praxic steps and said ok.
            if phase_str not in _PHASE_VALUES:
                return {
                    "ok": False,
                    "error": (
                        f"Invalid phase {step_data.get('phase')!r} on step {idx + 1}. "
                        f"Allowed: {', '.join(sorted(_PHASE_VALUES))}."
                    ),
                }
            phase = LessonPhase(phase_str)

            step = LessonStep(
                order=step_data.get("order", len(steps) + 1),
                phase=phase,
                action=step_data.get("action", ""),
                target=step_data.get("target"),
                code=step_data.get("code"),
                critical=step_data.get("critical", False),
                expected_outcome=step_data.get("expected_outcome"),
                error_recovery=step_data.get("error_recovery"),
                timeout_ms=step_data.get("timeout_ms"),
            )
            steps.append(step)

        # Create lesson
        lesson = Lesson(
            id=Lesson.generate_id(name, version),
            name=name,
            version=version,
            description=input_data.get("description", ""),
            epistemic=epistemic,
            steps=steps,
            domain=input_data.get("domain"),
            tags=input_data.get("tags", []),
            suggested_tier=input_data.get("suggested_tier", "free"),
            suggested_price=input_data.get("suggested_price", 0.0),
            created_by=input_data.get("created_by", "cli"),
            # Were never passed at all — the dataclass defaults won, so every
            # supplied value was discarded. Not a coercion; an omission.
            abstraction_level=input_data.get("abstraction_level", "personal"),
            sharing_policy=input_data.get("sharing_policy", "private"),
            abstract_pattern=input_data.get("abstract_pattern"),
        )

        # Store lesson. AMEND-IN-PLACE IS THE MODEL, and it is not obvious:
        # the id is DETERMINISTIC from (name, version) and create_lesson upserts
        # every layer (cold file overwritten, warm INSERT OR REPLACE, hot
        # reloaded, search point re-upserted). So re-publishing the same
        # name+version REPLACES the lesson — which is the amend path (no update
        # verb needed), and equally a way to clobber someone's lesson by reusing
        # a name. Silence made both invisible; `replaced` names which one
        # happened. Bump `version` to publish a revision alongside the original.
        storage = get_lesson_storage()
        replaced = storage.get_lesson(lesson.id) is not None
        result = storage.create_lesson(lesson)

        # Supersession, if declared. Validated against the store FIRST: an edge
        # to a lesson that does not exist suppresses nothing and reports success,
        # which is the shape where a practitioner believes the old guidance is
        # retired and it keeps being served. Reported either way — the receipt
        # says whether the edge was written, never just that the lesson was.
        supersedes = str(input_data.get("supersedes") or getattr(args, "supersedes", "") or "").strip() or None
        superseded_ok, supersede_error = _wire_supersession(storage, lesson.id, supersedes)

        # Return the STORED record, not a message. `ok: true` beside a
        # congratulatory string is not checkable; the caller had to read the
        # file back to discover the lesson was an empty shell. Echo what was
        # persisted so success and failure produce different, legible output.
        return {
            "ok": True,
            "lesson_id": lesson.id,
            "name": lesson.name,
            "version": lesson.version,
            # True = an existing lesson with this (name, version) was REPLACED.
            "replaced": replaced,
            "step_count": len(steps),
            "supersedes": supersedes,
            "supersedes_edge_written": superseded_ok,
            "supersedes_error": supersede_error,
            "cold_path": result.get("cold_path"),
            "elapsed_ms": result.get("elapsed_ms"),
            "stored": {
                "description_chars": len(lesson.description or ""),
                "steps": [{"order": s.order, "phase": s.phase.value} for s in lesson.steps],
                "domain": lesson.domain,
                "tags": list(lesson.tags or []),
                "abstraction_level": lesson.abstraction_level,
                "sharing_policy": lesson.sharing_policy,
                "abstract_pattern": lesson.abstract_pattern,
            },
        }

    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"Invalid JSON: {e}"}
    except Exception as e:
        logger.exception("Failed to create lesson")
        return {"ok": False, "error": str(e)}


def handle_lesson_load_command(args: Namespace) -> dict[str, Any]:
    """
    Load and display a lesson.

    Usage:
        empirica lesson-load --id <lesson_id>
        empirica lesson-load --id <lesson_id> --steps-only
    """
    from empirica.core.lessons import get_lesson_storage

    lesson_id = getattr(args, "id", None) or getattr(args, "lesson_id", None)
    if not lesson_id:
        return {"ok": False, "error": "Lesson ID required (--id)"}

    storage = get_lesson_storage()
    lesson = storage.get_lesson(lesson_id)

    if not lesson:
        return {"ok": False, "error": f"Lesson not found: {lesson_id}"}

    steps_only = getattr(args, "steps_only", False)

    if steps_only:
        steps = getattr(lesson, "steps", [])
        return {"ok": True, "lesson_id": lesson.id, "name": lesson.name, "steps": [s.to_dict() for s in steps]}

    to_dict_fn = getattr(lesson, "to_dict", None)
    return {"ok": True, "lesson": to_dict_fn() if to_dict_fn else {"id": lesson.id, "name": lesson.name}}


def handle_lesson_list_command(args: Namespace) -> dict[str, Any]:
    """
    List all lessons.

    Usage:
        empirica lesson-list
        empirica lesson-list --domain browser-automation
        empirica lesson-list --limit 20
    """
    from empirica.core.lessons import get_lesson_storage

    domain = getattr(args, "domain", None)
    limit = getattr(args, "limit", 20)

    include_superseded = bool(getattr(args, "include_superseded", False))

    storage = get_lesson_storage()
    lessons = storage.search_lessons(domain=domain, limit=limit, include_superseded=include_superseded)

    # Say what was withheld. A filter that drops rows without reporting the count
    # is indistinguishable from having nothing to show — the same false-clean
    # shape as a validator that silently skips a case.
    return {"ok": True, "count": len(lessons), "lessons": lessons, **_supersession_note(storage, include_superseded)}


def handle_lesson_search_command(args: Namespace) -> dict[str, Any]:
    """
    Search for lessons.

    Usage:
        empirica lesson-search --query "browser automation"
        empirica lesson-search --improves know
        empirica lesson-search --domain git
    """
    from empirica.core.lessons import get_lesson_storage

    query = getattr(args, "query", None)
    improves = getattr(args, "improves", None)
    domain = getattr(args, "domain", None)
    limit = getattr(args, "limit", 10)

    include_superseded = bool(getattr(args, "include_superseded", False))

    storage = get_lesson_storage()
    lessons = storage.search_lessons(
        query=query, domain=domain, improves_vector=improves, limit=limit, include_superseded=include_superseded
    )

    return {
        "ok": True,
        "query": query or improves or domain,
        "count": len(lessons),
        "lessons": lessons,
        **_supersession_note(storage, include_superseded),
    }


def handle_lesson_recommend_command(args: Namespace) -> dict[str, Any]:
    """
    Get lesson recommendations based on current epistemic state.

    Usage:
        empirica lesson-recommend --session-id <session_id>
        empirica lesson-recommend --know 0.4 --uncertainty 0.6
    """
    from empirica.core.lessons import get_lesson_storage

    # Get epistemic state from args or session
    epistemic_state = {}

    session_id = getattr(args, "session_id", None)
    if session_id:
        # Load from session's last PREFLIGHT
        from empirica.data.session_database import SessionDatabase

        db = SessionDatabase()
        cursor = db.adapter.conn.cursor()
        cursor.execute(
            """
            SELECT know, do, context, uncertainty
            FROM reflexes
            WHERE session_id = ? AND phase = 'PREFLIGHT'
            ORDER BY timestamp DESC LIMIT 1
        """,
            (session_id,),
        )
        row = cursor.fetchone()
        if row:
            epistemic_state = {
                "know": row[0] or 0,
                "do": row[1] or 0,
                "context": row[2] or 0,
                "uncertainty": row[3] or 0.5,
            }

    # Override with explicit args
    if getattr(args, "know", None) is not None:
        epistemic_state["know"] = args.know
    if getattr(args, "do", None) is not None:
        epistemic_state["do"] = args.do
    if getattr(args, "context", None) is not None:
        epistemic_state["context"] = args.context
    if getattr(args, "uncertainty", None) is not None:
        epistemic_state["uncertainty"] = args.uncertainty

    if not epistemic_state:
        return {"ok": False, "error": "Provide --session-id or epistemic vectors (--know, --do, etc.)"}

    threshold = getattr(args, "threshold", 0.6)
    storage = get_lesson_storage()
    recommendations = storage.find_best_lesson_for_gap(epistemic_state, threshold)

    return {"ok": True, "epistemic_state": epistemic_state, "threshold": threshold, "recommendations": recommendations}


def handle_lesson_stats_command(args: Namespace) -> dict[str, Any]:
    """
    Show lesson storage statistics.

    Usage:
        empirica lesson-stats
    """
    from empirica.core.lessons import get_lesson_storage

    storage = get_lesson_storage()
    stats = storage.stats()

    return {"ok": True, "stats": stats}


def handle_lesson_embed_command(args: Namespace) -> dict[str, Any]:
    """
    Embed all lessons into Qdrant for semantic search.

    Usage:
        empirica lesson-embed
        empirica lesson-embed --force  # Re-embed all
    """
    import empirica.core.lessons.storage as mod
    from empirica.core.lessons import get_lesson_storage

    # Clear singleton to force fresh Qdrant connection
    mod._storage = None

    storage = get_lesson_storage()

    if not storage._qdrant:
        return {"ok": False, "error": "Qdrant not available. Install qdrant-client."}

    getattr(args, "force", False)
    embedded = []
    failed = []

    # Get all lessons from WARM layer
    cursor = storage._conn.cursor()
    cursor.execute("SELECT id FROM lessons")
    lesson_ids = [row[0] for row in cursor.fetchall()]

    for lesson_id in lesson_ids:
        lesson = storage.get_lesson(lesson_id)
        if lesson:
            try:
                result = storage._write_search(lesson)
                if result:
                    embedded.append({"id": lesson_id, "name": lesson.name})
                else:
                    failed.append({"id": lesson_id, "error": "write failed"})
            except Exception as e:
                failed.append({"id": lesson_id, "error": str(e)})

    return {
        "ok": len(failed) == 0,
        "embedded_count": len(embedded),
        "failed_count": len(failed),
        "embedded": embedded,
        "failed": failed if failed else None,
        "collection": storage._qdrant_collection,
    }
