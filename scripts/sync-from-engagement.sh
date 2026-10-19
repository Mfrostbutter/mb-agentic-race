#!/usr/bin/env bash
# Mirror the agentic-race paths from the engagement repo into this one. The engagement repo is the source.
# Usage: scripts/sync-from-engagement.sh [path-to-engagement-repo]
set -euo pipefail
SRC="${1:-$HOME/Developer/engagements/mercedes-benz}"
DST="$(cd "$(dirname "$0")/.." && pwd)"
[ -d "$SRC/docs/agentic-race" ] || { echo "no agentic-race docs under $SRC" >&2; exit 1; }
rsync -a --delete --exclude '__pycache__' "$SRC/docs/agentic-race/" "$DST/docs/agentic-race/"
rsync -a --delete --exclude '__pycache__' "$SRC/workflows/agentic-race/" "$DST/workflows/agentic-race/"
cp "$SRC/scripts/ar-golden-run.py" "$DST/scripts/ar-golden-run.py"
cd "$DST" && git status --short
