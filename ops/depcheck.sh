#!/usr/bin/env bash
# Run after security deploys. Exits 1 if critical/high CVEs found.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AUDIT="$ROOT/.venv/bin/pip-audit"
GREP="$ROOT/.venv/bin/python"

if [[ ! -x "$AUDIT" ]]; then
  echo "pip-audit not found; install with: .venv/bin/pip install pip-audit" >&2
  exit 1
fi

echo "=== Python deps (pip-audit) ==="
OUT=$("$AUDIT" 2>&1)
echo "$OUT"
if echo "$OUT" | grep -qE "^(fastapi|starlette|httpx|anyio|pydantic|uvicorn) "; then
  echo "FAIL: key dep has a CVE" >&2
  exit 1
fi

echo ""
echo "=== JS date-math lint ==="
BAD=$(grep -rn --include="*.js" 'Date\.now() *[+\-] *[0-9]' "$ROOT/static/" 2>/dev/null | grep -v "\.min\.js" || true)
if [[ -n "$BAD" ]]; then
  echo "WARN: millisecond date arithmetic found (use setDate/setMonth for calendar math):"
  echo "$BAD"
fi

echo ""
echo "depcheck OK"
