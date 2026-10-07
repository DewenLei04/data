#!/usr/bin/env bash
# UniUSLaw — start the research service and map uniuslaw.com to this machine.
#   bash start_uniuslaw.sh          start
#   bash start_uniuslaw.sh --stop   stop
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SITE_HOST="${SITE_HOST:-uniuslaw.com}"
PORT="${PORT:-80}"
PIDFILE="${PIDFILE:-/tmp/uniuslaw.pid}"
LOGFILE="${LOGFILE:-/tmp/uniuslaw.log}"

# Service limits for this subscription tier. Set here rather than left to the
# caller so the tier applies however the service is started.
export FRICTION_RATE_LIMIT="${FRICTION_RATE_LIMIT:-7}"
export FRICTION_PAGINATION="${FRICTION_PAGINATION:-5}"
export FRICTION_SNIPPET_ONLY="${FRICTION_SNIPPET_ONLY:-1}"

if [[ "${1:-}" == "--stop" ]]; then
  [[ -f "$PIDFILE" ]] && kill "$(cat $PIDFILE)" 2>/dev/null || true
  rm -f "$PIDFILE"; echo "UniUSLaw stopped"; exit 0
fi

if ! grep -qE "[[:space:]]${SITE_HOST}(\$|[[:space:]])" /etc/hosts 2>/dev/null; then
  printf '127.0.0.1 %s www.%s\n' "$SITE_HOST" "$SITE_HOST" >> /etc/hosts 2>/dev/null || \
  sudo -n sh -c "printf '127.0.0.1 %s www.%s\n' '$SITE_HOST' '$SITE_HOST' >> /etc/hosts" 2>/dev/null || true
fi
if [[ "$PORT" == "80" ]] && [[ "$(id -u)" != "0" ]] && ! sudo -n true 2>/dev/null; then
  PORT=8741; echo "not root and no passwordless sudo; using port $PORT"
fi
DISPLAY_HOST="$SITE_HOST"; [[ "$PORT" != "80" ]] && DISPLAY_HOST="${DISPLAY_HOST}:${PORT}"

# server.py takes the display host from SITE_HOST, not from a flag.
SITE_HOST="$DISPLAY_HOST" python3 "$HERE/server.py" --db "$HERE/data/us_law.db" \
        --port "$PORT" > "$LOGFILE" 2>&1 &
echo $! > "$PIDFILE"
for _ in $(seq 1 40); do
  python3 -c "import socket,sys; s=socket.socket(); s.settimeout(0.3); \
sys.exit(0 if s.connect_ex(('127.0.0.1',$PORT))==0 else 1)" \
    && { echo "UniUSLaw ready → http://${DISPLAY_HOST}/"; exit 0; }
  sleep 0.25
done
echo "failed to start; log follows:"; cat "$LOGFILE"; exit 1
