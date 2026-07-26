#!/bin/sh
# api runs migrations before serving (RUN_MIGRATIONS=1); worker leaves it unset and
# just execs its command — the schema is in place before any job is deferred.
set -e

# A named volume takes its ownership from the image directory it mounts over, and
# the Dockerfile chowns /data to 10001 for exactly that reason — so a fresh volume
# is fine. A volume created by an OLDER image is not: it keeps root ownership, the
# container runs as `app`, and the first upload dies with EACCES inside a request
# handler, surfacing as a 500 with no hint about why. We cannot chown it (not
# root, by design), so fail loudly at boot instead of obscurely at upload.
for d in "${UPLOADS_DIR:-/data/uploads}" "${ARTIFACTS_DIR:-/data/artifacts}"; do
    if [ -d "$d" ] && [ ! -w "$d" ]; then
        echo "[entrypoint] FATAL: $d is not writable by uid $(id -u)." >&2
        echo "[entrypoint] A volume from an older image kept root ownership. Fix with:" >&2
        echo "[entrypoint]   docker run --rm -v <volume>:/v alpine chown -R 10001:10001 /v" >&2
        exit 1
    fi
done

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    echo "[entrypoint] alembic upgrade head"
    alembic upgrade head
fi
exec "$@"
