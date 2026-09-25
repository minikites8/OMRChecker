#!/usr/bin/env bash
set -euo pipefail

repo="${1:?usage: ROLLBACK.sh <repository-path> }"
cd "$repo"
git restore --source=HEAD -- \
  backend/app.py \
  scan_ui.py \
  src/tests/test_review_endpoint.py \
  src/tests/test_scan_ui.py \
  ui/index.html
printf 'ROLLBACK_RESTORED=%s\n' "$repo"
git diff --exit-code -- \
  backend/app.py \
  scan_ui.py \
  src/tests/test_review_endpoint.py \
  src/tests/test_scan_ui.py \
  ui/index.html