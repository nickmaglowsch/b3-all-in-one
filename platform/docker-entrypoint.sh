#!/bin/sh
# api runs migrations before serving (RUN_MIGRATIONS=1); worker leaves it unset and
# just execs its command — the schema is in place before any job is deferred.
set -e
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    echo "[entrypoint] alembic upgrade head"
    alembic upgrade head
fi
exec "$@"
