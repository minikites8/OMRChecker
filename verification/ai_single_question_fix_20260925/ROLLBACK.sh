#!/usr/bin/env bash
set -euo pipefail
TARGET_ROOT="${1:?workspace path required}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
copy_file() { mkdir -p "$(dirname -- "$TARGET_ROOT/$1")"; cp "$SCRIPT_DIR/baseline/${1//\//__}" "$TARGET_ROOT/$1"; }
copy_file exam_review.py
copy_file scan_ui.py
copy_file backend/app.py
copy_file ui/app.js
copy_file ui/app.css
copy_file ui/index.html
rm -f "$TARGET_ROOT/src/tests/test_ai_single_question.py" "$TARGET_ROOT/ui/tests/single-ai.test.cjs"
printf 'ROLLBACK_OK target=%s\n' "$TARGET_ROOT"
