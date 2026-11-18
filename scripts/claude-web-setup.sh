#!/usr/bin/env bash
# SessionStart hook (.claude/settings.json): prepares Claude Code on the web (claude.ai/code) sessions.
# The cloud sandbox starts from a fresh clone, so install the Python test dependencies there.
# Does nothing on local machines (CLAUDE_CODE_REMOTE is only "true" in web sessions).
set -euo pipefail
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/..}"
python3 -m pip install --quiet --disable-pip-version-check -r backend/requirements-dev.txt
echo "Muddy Points: test dependencies installed. Run tests with: cd backend && python3 -m pytest"
