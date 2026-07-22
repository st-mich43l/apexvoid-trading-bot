#!/bin/sh
set -u

mkdir -p /var/lib/apexvoid \
  || echo "ctrader-feed WARNING token mirror directory creation failed" >&2
chown app:app /var/lib/apexvoid \
  || echo "ctrader-feed WARNING token mirror directory ownership update failed" >&2
chmod 700 /var/lib/apexvoid \
  || echo "ctrader-feed WARNING token mirror directory mode update failed" >&2

exec setpriv --reuid=app --regid=app --init-groups /app/ctrader-feed "$@"
