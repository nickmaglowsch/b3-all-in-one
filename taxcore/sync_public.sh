#!/usr/bin/env bash
#
# sync_public.sh — mirror ONLY the logic of this private repo into the public
# template repo (~/b3-portfolio-public), never personal data.
#
# Safety model (defense in depth):
#   1. ALLOWLIST  — only the paths in PATHS[] below are ever copied. Personal
#                   data (b3_imports/, data/, reports/, *.sqlite) is never on
#                   the list, so it cannot leak. A denylist could miss a new
#                   file; an allowlist cannot.
#   2. TRIPWIRE   — after copying, scan the public tree for CPF / email / name
#                   and ABORT before staging if anything matches.
#   3. .gitignore — the public repo keeps its OWN .gitignore (which blocks
#                   data/, b3_imports/, reports/). This script never copies it.
#   4. NO PUSH    — copies, scans, stages, shows the diff, then stops. You
#                   review and `git commit && git push` from the public repo.
#
# Usage:  ./sync_public.sh        (dry-run-ish: copies, scans, stages, shows diff)
#
set -euo pipefail

PRIV="$HOME/b3-portfolio"
PUB="$HOME/b3-portfolio-public"

# --- Allowlist: ONLY logic. Add new logic paths here; never add data paths. ---
PATHS=(
  scripts
  methodologies
  pyproject.toml
  uv.lock
  README.md
  AGENTS.md
  CLAUDE.md
)

# Paths that must NEVER be synced, asserted explicitly as a second guard.
FORBIDDEN=(b3_imports data reports http_cache.sqlite .gitignore .git)

[ -d "$PUB/.git" ] || { echo "ABORT: $PUB is not a git repo"; exit 1; }

echo "==> Mirroring logic from $PRIV  ->  $PUB"
for p in "${PATHS[@]}"; do
  # Guard: refuse if someone added a forbidden path to the allowlist.
  for f in "${FORBIDDEN[@]}"; do
    [ "$p" = "$f" ] && { echo "ABORT: '$p' is forbidden and must not be synced"; exit 1; }
  done

  src="$PRIV/$p"
  [ -e "$src" ] || { echo "  skip (missing in private): $p"; continue; }

  if [ -d "$src" ]; then
    rsync -a --delete \
      --exclude='__pycache__/' --exclude='*.py[cod]' \
      --exclude='.DS_Store' --exclude='*.egg-info/' \
      "$src/" "$PUB/$p/"
    echo "  dir  $p/"
  else
    cp "$src" "$PUB/$p"
    echo "  file $p"
  fi
done

# --- Tripwire: scan ONLY the synced logic for personal markers. ------------
echo "==> Scanning public tree for personal data..."
cd "$PUB"
PII=$(grep -rInE \
  -e '[0-9]{3}\.[0-9]{3}\.[0-9]{3}-[0-9]{2}' \
  -e 'nmaglowsch' -e 'Salgueiro' -e 'necc\.org' \
  scripts methodologies ./*.md 2>/dev/null | grep -v __pycache__ || true)
if [ -n "$PII" ]; then
  echo "ABORT: possible personal data found in synced files — nothing staged:"
  echo "$PII"
  exit 1
fi
echo "  clean (no CPF / email / name in synced files)"

# --- Stage and show, but DO NOT commit or push. ----------------------------
git add scripts methodologies pyproject.toml uv.lock README.md AGENTS.md CLAUDE.md
echo "==> Staged changes in public repo:"
git -C "$PUB" diff --cached --stat
echo
echo "Review the diff, then from $PUB run:"
echo "    git diff --cached            # full review"
echo "    git commit -m 'Sync logic from private repo'"
echo "    git push"
