#!/usr/bin/env bash
# Node policy check: native nodes first. RESULT: ok or the findings, exit 1 on an error.
# Usage: scripts/node-policy.sh [workflow.json | directory] [--policy deploy/node-policy.json] [--json]
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$HERE/.agents/skills/n8n-native-nodes-first/scripts/node_policy_check.py" "${@:-$HERE/workflows/voice-booking}"
