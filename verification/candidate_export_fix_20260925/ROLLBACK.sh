#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:?workspace root is required}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$ROOT/ui" "$ROOT/src/tests" "$ROOT/backend" "$ROOT/ui/tests"
cp "$SCRIPT_DIR/original/candidates.js" "$ROOT/ui/candidates.js"
cp "$SCRIPT_DIR/original/scan_ui.py" "$ROOT/scan_ui.py"
cp "$SCRIPT_DIR/original/index.html" "$ROOT/ui/index.html"
cp "$SCRIPT_DIR/original/test_candidates.py" "$ROOT/src/tests/test_candidates.py"
cp "$SCRIPT_DIR/original/backend__app.py" "$ROOT/backend/app.py"
cp "$SCRIPT_DIR/original/ui__tests__candidates.test.cjs" "$ROOT/ui/tests/candidates.test.cjs"
rm -f "$ROOT/src/tests/test_candidate_export.py"
printf 'ROLLBACK_RESTORED=%s\n' "$ROOT"
