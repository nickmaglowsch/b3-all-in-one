#!/usr/bin/env bash
# Seed a dev tenant end-to-end against the running API:
#   health → dev-login → upload all B3 extracts → wait imports → set sleeves →
#   run one backtest → wait → summary.
# Idempotent: imports full-replace the portfolio; re-running is safe.
#
#   docker compose --profile app up -d   # bring the API up first
#   scripts/dev_seed.sh
set -euo pipefail

API="${API_URL:-http://localhost:8000}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMPORTS_DIR="$REPO_ROOT/taxcore/b3_imports"

log() { printf '  %s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }

# Extract one JSON field from stdin without needing jq.
jget() { python3 -c "import sys,json; print(json.load(sys.stdin)$1)"; }

step "Waiting for API health at $API"
for i in $(seq 1 60); do
  if curl -sf "$API/health" >/dev/null 2>&1; then
    log "healthy"
    break
  fi
  [ "$i" = 60 ] && { echo "API never became healthy" >&2; exit 1; }
  sleep 1
done

step "Dev login"
TOKEN="$(curl -sf -X POST "$API/auth/dev-login" | jget "['access_token']")"
AUTH=(-H "Authorization: Bearer $TOKEN")
log "token acquired (${#TOKEN} chars)"

step "Uploading B3 extracts from $IMPORTS_DIR"
FILES=()
for f in "$IMPORTS_DIR"/*.xlsx; do
  [ -e "$f" ] || { echo "no .xlsx in $IMPORTS_DIR" >&2; exit 1; }
  FILES+=(-F "files=@$f")
done
UPLOADED="$(curl -sf "${AUTH[@]}" -X POST "$API/imports" "${FILES[@]}" \
  | python3 -c 'import sys,json;print(len(json.load(sys.stdin)))')"
log "uploaded $UPLOADED file(s)"

step "Waiting for imports to finish"
for i in $(seq 1 120); do
  SUMMARY="$(curl -sf "${AUTH[@]}" "$API/imports" | python3 -c '
import sys, json
rows = json.load(sys.stdin)
from collections import Counter
c = Counter(r["status"] for r in rows)
busy = c.get("pending", 0) + c.get("processing", 0)
print(busy, dict(c))
')"
  BUSY="${SUMMARY%% *}"
  if [ "$BUSY" = "0" ]; then
    log "imports settled: ${SUMMARY#* }"
    break
  fi
  [ "$i" = 120 ] && { echo "imports did not finish: ${SUMMARY#* }" >&2; exit 1; }
  sleep 2
done

step "Setting sleeves (MomSharpe 60 / CASH_CDI 40)"
curl -sf "${AUTH[@]}" -X PUT "$API/portfolio/sleeves" \
  -H "Content-Type: application/json" \
  -d '[{"strategy_ref":"MomSharpe","target_pct":60},{"strategy_ref":"CASH_CDI","target_pct":40}]' \
  >/dev/null
log "sleeves saved"

step "Running one backtest (MomSharpe)"
RUN_ID="$(curl -sf "${AUTH[@]}" -X POST "$API/backtests" \
  -H "Content-Type: application/json" \
  -d '{"strategy":"MomSharpe"}' | jget "['id']")"
log "run $RUN_ID queued"

for i in $(seq 1 150); do
  STATUS="$(curl -sf "${AUTH[@]}" "$API/backtests/$RUN_ID" | jget "['status']")"
  case "$STATUS" in
    done) log "backtest done"; break ;;
    error)
      ERR="$(curl -sf "${AUTH[@]}" "$API/backtests/$RUN_ID" | jget "['error']")"
      echo "backtest failed: $ERR" >&2; exit 1 ;;
  esac
  [ "$i" = 150 ] && { echo "backtest did not finish (status=$STATUS)" >&2; exit 1; }
  sleep 2
done

step "Summary"
curl -sf "${AUTH[@]}" "$API/backtests/$RUN_ID" | python3 -c '
import sys, json
r = json.load(sys.stdin)
m = r.get("metrics") or {}
pre, aft = m.get("pretax") or {}, m.get("aftertax") or {}
print("  strategy   :", r["strategy"])
print("  status     :", r["status"])
if pre:
    print("  ann.return : pre %.2f%%  /  after %.2f%%" % (
        pre.get("Ann. Return (%)", 0.0), aft.get("Ann. Return (%)", 0.0)))
    print("  sharpe     : pre %.2f  /  after %.2f" % (
        pre.get("Sharpe", 0.0), aft.get("Sharpe", 0.0)))
    print("  max dd     : %.2f%%" % pre.get("Max Drawdown (%)", 0.0))
if "total_tax_paid" in m:
    print("  tax paid   : R$ %.2f" % m["total_tax_paid"])
'
POS="$(curl -sf "${AUTH[@]}" "$API/portfolio/positions" \
  | python3 -c 'import sys,json;print(len(json.load(sys.stdin)))' 2>/dev/null || echo '?')"
log "positions   : ${POS}"
printf '\nSeed complete. Open the frontend and log in with "Entrar (dev)".\n'
