#!/usr/bin/env bash
# Install empirica's git hooks into this clone's .git/hooks/.
#
# Idempotent + composable: if a beads-managed pre-push already exists, it is
# preserved as pre-push.beads so our hook can delegate to it (our ruff gate
# runs first, then the beads JSONL-sync check). Re-running is safe.
#
# Note: .git/hooks/ is not version-controlled, so this is a per-clone step.
# `bd` may re-install its own pre-push on beads operations, which would drop
# our ruff layer until you re-run `make install-hooks` — a graceful downgrade,
# not a breakage.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK_DIR="$(git rev-parse --git-path hooks)"
SRC="$REPO_ROOT/scripts/git-hooks/pre-push"
DEST="$HOOK_DIR/pre-push"

mkdir -p "$HOOK_DIR"

# Preserve an existing beads pre-push so our hook can delegate to it.
if [ -f "$DEST" ] && grep -qE 'bd-hooks-version|beads' "$DEST" 2>/dev/null; then
    if [ ! -f "$HOOK_DIR/pre-push.beads" ]; then
        cp "$DEST" "$HOOK_DIR/pre-push.beads"
        chmod +x "$HOOK_DIR/pre-push.beads"
        echo "preserved existing beads pre-push → $HOOK_DIR/pre-push.beads"
    fi
fi

cp "$SRC" "$DEST"
chmod +x "$DEST"
echo "installed CI-parity pre-push hook → $DEST"
echo "  (runs ruff check + format --check before every push; delegates to beads if present)"
