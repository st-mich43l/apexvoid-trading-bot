#!/bin/sh
set -u

# Bind mounts are created by Docker as root when the host directory is absent.
# Repair both writable mounts before dropping privileges so file logging and
# the durable publication ledger work after a clean deploy as well as locally.
for dir in /var/log/apexvoid /var/lib/apexvoid-analysis-engine; do
    mkdir -p "$dir" 2>/dev/null || true
    chown -R apexvoid:apexvoid "$dir" 2>/dev/null || true
done

exec su-exec apexvoid "$@"
