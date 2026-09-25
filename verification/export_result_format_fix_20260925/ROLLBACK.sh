#!/usr/bin/env bash
set -euo pipefail
TARGET_ROOT="${1:?workspace path required}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
copy_file() {
  local relative="$1"
  local source_name="${relative////__}"
  mkdir -p "$(dirname -- "$TARGET_ROOT/$relative")"
  cp "$SCRIPT_DIR/baseline/$source_name" "$TARGET_ROOT/$relative"
}
copy_file scan_ui.py
copy_file src/tests/test_candidate_export.py
copy_file src/tests/test_candidates.py
copy_file ui/tests/candidates.test.cjs
printf 'ROLLBACK_OK target=%s\n' "$TARGET_ROOT"
