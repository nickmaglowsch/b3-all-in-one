#!/usr/bin/env bash
# End-to-end smoke of the dockerized stack: clean up --build -> import 3 real B3
# extracts -> assert portfolio positions + an IRPF report come back.
#   ./scripts/docker_smoke.sh          # tears the stack down at the end (keeps volumes)
#   ./scripts/docker_smoke.sh --keep   # leaves the stack running for inspection
set -euo pipefail

KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

API=http://localhost:8000
B3=taxcore/b3_imports
# 2 negociacao + 1 movimentacao — enough for non-empty positions and a full IRPF run.
FILES=(
  "$B3/negociacao-2026-05-08-17-48-45.xlsx"
  "$B3/negociacao-2026-05-08-17-49-14.xlsx"
  "$B3/movimentacao-2026-05-08-18-45-28.xlsx"
)
XLSX_MIME="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

teardown() {
  if [ "$KEEP" = "1" ]; then
    echo "[smoke] --keep: leaving stack running"
  else
    echo "[smoke] tearing down (postgres/uploads volumes kept)"
    docker compose --profile app down
  fi
}
trap teardown EXIT

fail() { echo "[smoke] FAIL: $*" >&2; exit 1; }

t0=$SECONDS
echo "[smoke] docker compose --profile app up --build -d"
docker compose --profile app up --build -d

# 1) health (api only serves once `alembic upgrade head` + uvicorn are up)
echo "[smoke] waiting for /health ..."
for i in $(seq 1 60); do
  curl -fsS "$API/health" >/dev/null 2>&1 && break
  [ "$i" = "60" ] && fail "api /health not ready after 120s"
  sleep 2
done
t_health=$((SECONDS - t0))
echo "[smoke] health OK (${t_health}s)"

# 2) dev-login
TOKEN=$(curl -fsS -X POST "$API/auth/dev-login" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])') \
  || fail "dev-login failed"
[ -n "$TOKEN" ] || fail "empty access_token"
AUTH="Authorization: Bearer $TOKEN"

# 3) upload the 3 fixtures (retry: worker may still be applying the procrastinate schema
#    that the deferred job needs)
FF=()
for f in "${FILES[@]}"; do
  [ -f "$f" ] || fail "missing fixture: $f"
  FF+=(-F "files=@${f};type=${XLSX_MIME}")
done
echo "[smoke] POST /imports (${#FILES[@]} files)"
IMPORTED=0
for i in $(seq 1 15); do
  if curl -fsS -X POST "$API/imports" -H "$AUTH" "${FF[@]}" -o /dev/null; then
    IMPORTED=1; break
  fi
  echo "[smoke] /imports not ready yet, retry $i"
  sleep 2
done
[ "$IMPORTED" = "1" ] || fail "POST /imports failed after retries"

# 4) poll until every import row is done (or surface an error), timeout 120s
echo "[smoke] polling /imports ..."
t_imp=$SECONDS
while :; do
  ST=$(curl -fsS "$API/imports" -H "$AUTH" | python3 -c '
import sys, json
rows = json.load(sys.stdin)
sts = {r["status"] for r in rows}
if not rows:
    print("empty")
elif "error" in sts:
    errs = [r.get("error") or "" for r in rows if r["status"] == "error"]
    print("error:" + errs[0].replace(chr(10), " ")[:300])
elif sts == {"done"}:
    print("done")
else:
    print("pending:" + ",".join(sorted(sts)))
') || fail "GET /imports failed"
  case "$ST" in
    done) echo "[smoke] imports done ($((SECONDS - t_imp))s)"; break ;;
    error:*) fail "import errored: ${ST#error:}" ;;
    *) : ;;
  esac
  [ $((SECONDS - t_imp)) -ge 120 ] && fail "imports not done within 120s (last: $ST)"
  sleep 2
done

# 5) positions must be non-empty
NPOS=$(curl -fsS "$API/portfolio/positions" -H "$AUTH" \
  | python3 -c 'import sys,json;print(len(json.load(sys.stdin)))') \
  || fail "GET /portfolio/positions failed"
[ "$NPOS" -gt 0 ] || fail "portfolio/positions is empty"
echo "[smoke] positions: $NPOS"

# 6) IRPF report must contain "Bens e Direitos"
MD=$(curl -fsS -X POST "$API/irpf/reports" -H "$AUTH" \
  -H 'Content-Type: application/json' -d '{"year":2025}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["markdown"])') \
  || fail "POST /irpf/reports failed"
printf '%s' "$MD" | grep -q "Bens e Direitos" \
  || fail "IRPF report missing 'Bens e Direitos'"
echo "[smoke] IRPF report OK (${#MD} chars, contains 'Bens e Direitos')"

echo "[smoke] =================================================="
echo "[smoke] PASS  health=${t_health}s  imports=$((SECONDS - t_imp))s  positions=$NPOS  total=$((SECONDS - t0))s"
echo "[smoke] =================================================="
