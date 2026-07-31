#!/bin/sh
set -u

mkdir -p /var/lib/apexvoid \
  || echo "ctrader-feed WARNING token mirror directory creation failed" >&2
chown app:app /var/lib/apexvoid \
  || echo "ctrader-feed WARNING token mirror directory ownership update failed" >&2
chmod 700 /var/lib/apexvoid \
  || echo "ctrader-feed WARNING token mirror directory mode update failed" >&2

# Host-mounted log directory — service rotates files itself (DailyFileLog).
mkdir -p /var/log/apexvoid \
  || echo "ctrader-feed WARNING log directory creation failed" >&2
if id app >/dev/null 2>&1; then
  chown -R app:app /var/log/apexvoid \
    || echo "ctrader-feed WARNING log directory ownership update failed" >&2
  chmod 755 /var/log/apexvoid \
    || echo "ctrader-feed WARNING log directory mode update failed" >&2
  exec setpriv --reuid=app --regid=app --init-groups /app/ctrader-feed "$@"
fi

exec /app/ctrader-feed "$@"
