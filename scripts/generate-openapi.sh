#!/usr/bin/env bash
# Dump the FastAPI OpenAPI schema to openapi.json at the backend root.
# Consumed by the frontend npm run types:generate script.
# NOTE: /openapi.json (served by FastAPI at app root) is intentionally PUBLIC —
# it does not sit under /api/v1/ and therefore bypasses verify_api_key.
# Do not move it behind the authenticated router.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
python -c "from app.main import app; import json; print(json.dumps(app.openapi()))" > openapi.json
echo "openapi.json generated ($(wc -c < openapi.json) bytes)"
