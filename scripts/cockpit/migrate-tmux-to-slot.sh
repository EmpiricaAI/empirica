#!/usr/bin/env bash
# Migrate per-instance state from tmux_N → slot name.
# Run AFTER `cockpit kill`, BEFORE `cockpit` relaunch.
#
# Usage: ./migrate-tmux-to-slot.sh tmux_1 cortex
#                                  ^FROM   ^TO
#
# Renames every per-instance state file from FROM_id to TO_id:
#   loops_FROM.json                          → loops_TO.json
#   listeners_FROM.json                      → listeners_TO.json
#   instance_projects/FROM.json              → instance_projects/TO.json
#   active_session_FROM                      → active_session_TO
#   loop_install_pending_FROM_*.json         → loop_install_pending_TO_*.json
#   loop_uninstall_pending_FROM_*.json       → loop_uninstall_pending_TO_*.json
#   loop_paused_FROM_*                       → loop_paused_TO_*
#   listener_install_pending_FROM_*.json     → ...
#   listener_active_FROM_*.json              → ...
#   canonical_loops_installed_FROM           → canonical_loops_installed_TO
#   hook_counters_FROM.json                  → hook_counters_TO.json
#   context_usage_FROM.json                  → ...

set -euo pipefail
FROM="${1:?from instance_id required}"
TO="${2:?to instance_id required}"
DIR="$HOME/.empirica"

for pattern in \
    "loops_${FROM}.json" \
    "listeners_${FROM}.json" \
    "instance_projects/${FROM}.json" \
    "active_session_${FROM}" \
    "canonical_loops_installed_${FROM}" \
    "hook_counters_${FROM}.json" \
    "context_usage_${FROM}.json"; do
  src="$DIR/$pattern"
  if [ -e "$src" ]; then
    dst=$(echo "$pattern" | sed "s/${FROM}/${TO}/g")
    mv "$src" "$DIR/$dst"
    echo "  $pattern → $dst"
  fi
done

# Multi-segment files (instance + loop/listener name)
for glob in "loop_install_pending_${FROM}_"*.json \
            "loop_uninstall_pending_${FROM}_"*.json \
            "loop_paused_${FROM}_"* \
            "listener_install_pending_${FROM}_"*.json \
            "listener_active_${FROM}_"*.json; do
  for f in "$DIR"/$glob; do
    [ -e "$f" ] || continue
    base=$(basename "$f")
    new=$(echo "$base" | sed "s/${FROM}/${TO}/")
    mv "$f" "$DIR/$new"
    # A pending request names the instance it was written for in its payload too, and list_pending matches on THAT. Renaming
    # only the file left the request owned by an instance that no longer exists, so it was never surfaced or consumed.
    case "$new" in *.json) sed -i "s/\"instance_id\": \"${FROM}\"/\"instance_id\": \"${TO}\"/" "$DIR/$new" ;; esac
    echo "  $base → $new"
  done
done

echo "Migration complete: $FROM → $TO"
