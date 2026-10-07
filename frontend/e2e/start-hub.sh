#!/bin/sh
# Starts a throwaway hub for the end-to-end tests (Playwright runs this; see playwright.config.js):
# SQLite in frontend/.e2e-data (wiped first), the cameras in e2e/devices.toml, the built
# dashboard, and a test admin whose password comes from E2E_ADMIN_PASSWORD.
set -eu

FRONTEND=$(cd "$(dirname "$0")/.." && pwd)
REPO=$(dirname "$FRONTEND")
DATA="$FRONTEND/.e2e-data"
rm -rf "$DATA"
mkdir -p "$DATA"

HASH=$(cd "$REPO" && E2E_ADMIN_PASSWORD="$E2E_ADMIN_PASSWORD" uv run --frozen python -c \
  "import os; from pwdlib import PasswordHash; print(PasswordHash.recommended().hash(os.environ['E2E_ADMIN_PASSWORD']))")

export VISION_HUB_APP__PORT="${E2E_PORT:-8765}"
export VISION_HUB_APP__LOG_LEVEL=WARNING
export VISION_HUB_APP__DASHBOARD_DIR="$FRONTEND/dist"
export VISION_HUB_DB__URL="sqlite+aiosqlite:///$DATA/hub.db"
export VISION_HUB_VISION__DEVICES_FILE="$FRONTEND/e2e/devices.toml"
export VISION_HUB_SECURITY__ADMIN_PASSWORD_HASH="$HASH"
export VISION_HUB_SECURITY__JWT_SECRET="e2e-only-secret-that-is-long-enough-for-hs256-0123456789"
# Each test signs in afresh; the production limit (5/minute) would throttle the suite.
export VISION_HUB_SECURITY__AUTH_RATE_LIMIT=200/minute

# Real push services are out of reach from tests: subscriptions are faked onto this name, which
# never resolves (e2e/push.e2e.js), so sending to them fails like an unreachable service.
export VISION_HUB_PUSH__ALLOWED_HOSTS=push.e2e.invalid
export VISION_HUB_PUSH__SUBJECT=mailto:e2e@example.com

# The person model, if downloaded (`vision-hub download-model`); without it cameras alert on all
# motion, and the tests pass either way (the synthetic visitors are not people).
export VISION_HUB_PERSONS__MODEL_PATH="$REPO/data/models/person-yolox-s.onnx"

# Relative data paths (snapshots, keys, the lock file) land in the throwaway folder.
cd "$DATA"
exec uv run --project "$REPO" --frozen vision-hub serve
