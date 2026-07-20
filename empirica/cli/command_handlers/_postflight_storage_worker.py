"""Detached worker for the POSTFLIGHT storage pipeline.

Spawned fire-and-forget by the POSTFLIGHT handler so the storage pipeline
(embeddings + global sync + snapshot — measured ~5.7s) runs OFF the POSTFLIGHT
response critical path. The parent writes the pipeline inputs to a JSON payload
file and execs this module detached (start_new_session); we read the payload,
delete the temp file, and run the pipeline to completion in the background.

Eventual-consistency by design: embeddings / global-sync land a moment after
POSTFLIGHT returns. Nothing reads them back synchronously (the in-session AI has
already closed the loop; retrieval is future-tense), so the AI gets its response
~5.7s sooner without losing any storage work.
"""

from __future__ import annotations

import json
import os
import sys


def main(payload_path: str) -> None:
    try:
        with open(payload_path, encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return  # unreadable payload → nothing to do
    finally:
        # Temp file is single-use; drop it whether or not the load succeeded.
        try:
            os.unlink(payload_path)
        except Exception:
            pass

    # Import lazily so the detached process only pays for what it runs.
    from empirica.cli.command_handlers._workflow_postflight import _run_postflight_storage_pipeline

    _run_postflight_storage_pipeline(
        session_id=payload["session_id"],
        vectors=payload["vectors"],
        deltas=payload["deltas"],
        reasoning=payload["reasoning"],
        grounded_verification=payload["grounded_verification"],
        postflight_confidence=payload["postflight_confidence"],
        checkpoint_id=payload["checkpoint_id"],
    )


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(sys.argv[1])
