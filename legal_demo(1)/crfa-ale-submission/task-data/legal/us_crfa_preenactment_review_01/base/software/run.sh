#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$HERE/data/us_law.db" ]] || { echo 'data/us_law.db missing — package is incomplete' >&2; exit 1; }
exec python3 "$HERE/server.py" --db "$HERE/data/us_law.db" --port "${PORT:-8741}" "$@"
