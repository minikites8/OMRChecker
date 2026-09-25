#!/usr/bin/env bash
set -euo pipefail

repo="${1:?usage: ROLLBACK.sh <repository-path> }"
cd "$repo"
git restore --source=HEAD -- \
  ai_judge.py \
  runtime_settings.py \
  src/tests/test_ai_judge.py \
  src/tests/test_runtime_settings.py \
  docs/admin-panel.md \
  deploy/README.md
printf 'ROLLBACK_RESTORED=%s\n' "$repo"
git diff --exit-code -- \
  ai_judge.py \
  runtime_settings.py \
  src/tests/test_ai_judge.py \
  src/tests/test_runtime_settings.py \
  docs/admin-panel.md \
  deploy/README.md