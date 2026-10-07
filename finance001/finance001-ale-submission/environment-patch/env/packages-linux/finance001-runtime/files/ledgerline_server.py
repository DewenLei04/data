#!/usr/bin/env python3
"""Ledgerline -- offline point-in-time replay of the cap-table data terminal.

Standard library only (http.server, json) -- see Phase 4 PLAN.md, "Ledgerline API
contract". Every response is HTTP 200 with an envelope:

    {"code": "OK", "message": "", "total": 147, "returned": 50, "offset": 0,
     "has_more": true, "truncated": false, "data": [ ... ]}

HTTP status is always 200 except a missing/wrong `X-Api-Key`, which is 401. Business
codes: OK, RATE_LIMITED, BAD_REQUEST, NOT_FOUND, UNAUTHORIZED.

Throttle (J2/J3): a single process-wide monotonic counter increments on every
data-bearing request (never /healthz). When counter % 7 == 0 the response is
RATE_LIMITED with empty data and no cursor movement -- a retry lands on the next
counter value and succeeds. This is a counter, never `random`, so two identical call
sequences throttle at identical indices.

Batch truncation (J4): POST /v1/certificates/batch above 25 ids returns the first 25
ids in request order, `truncated: true`; at or below 25, all requested (that exist)
are returned.

Usage:
    python3 ledgerline_server.py --snapshot /opt/ledgerline/snapshot --port 8091

Auth: header X-Api-Key. Value resolution order (first that applies wins):
    1. env var LEDGERLINE_API_KEY
    2. /etc/ledgerline_api_key (mode 0444 in the built image, per J8)
    3. literal "test" (placeholder fallback for standalone/dev runs before either
       of the above exists, e.g. this file's own verification gate)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULT_LIMIT = 50
MAX_LIMIT = 50
BATCH_MAX = 25
THROTTLE_EVERY = 7

RESOURCES = ("certificates", "option_grants", "awards", "holders")

# ---- process-wide deterministic throttle -----------------------------------------
_counter_lock = threading.Lock()
_counter = 0


def _bump_counter() -> int:
    """Increment the single process-wide, data-bearing-request counter and
    return its new value. Never random -- a plain monotonic count, so the same
    sequence of calls always throttles at the same indices."""
    global _counter
    with _counter_lock:
        _counter += 1
        return _counter


def _is_throttled() -> bool:
    return _bump_counter() % THROTTLE_EVERY == 0


# ---- fixtures ----------------------------------------------------------------------
def load_snapshot(snapshot_dir: Path) -> dict:
    data = {}
    for name in RESOURCES:
        p = snapshot_dir / f"{name}.json"
        if p.exists():
            data[name] = json.loads(p.read_text(encoding="utf-8"))
        else:
            data[name] = []
    return data


def resolve_api_key() -> str:
    env_key = os.environ.get("LEDGERLINE_API_KEY")
    if env_key:
        return env_key
    key_file = Path("/etc/ledgerline_api_key")
    if key_file.exists():
        try:
            text = key_file.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            pass
    return "test"


# ---- helpers -------------------------------------------------------------------------
def envelope(code="OK", message="", total=0, returned=0, offset=0,
             has_more=False, truncated=False, data=None):
    return {
        "code": code,
        "message": message,
        "total": total,
        "returned": returned,
        "offset": offset,
        "has_more": has_more,
        "truncated": truncated,
        "data": data if data is not None else [],
    }


def clamp_limit(raw) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    if v <= 0:
        return DEFAULT_LIMIT
    return min(v, MAX_LIMIT)


def clamp_offset(raw) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(v, 0)


class LedgerlineHandler(BaseHTTPRequestHandler):
    server_version = "Ledgerline/1.0"
    protocol_version = "HTTP/1.1"

    # Populated by main() before the server starts serving.
    DATA: dict = {}
    API_KEY: str = ""

    # -- logging -----------------------------------------------------------------
    def log_message(self, fmt, *args):  # noqa: N802 (BaseHTTPRequestHandler API)
        sys.stderr.write("[ledgerline] " + (fmt % args) + "\n")
        sys.stderr.flush()

    def _log_business(self, path: str, code: str) -> None:
        sys.stderr.write(f"[ledgerline] {path} -> {code}\n")
        sys.stderr.flush()

    # -- auth ----------------------------------------------------------------------
    def _authorized(self) -> bool:
        key = self.headers.get("X-Api-Key")
        return bool(key) and key == self.API_KEY

    # -- response helpers ------------------------------------------------------------
    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _unauthorized(self, path: str) -> None:
        self._log_business(path, "UNAUTHORIZED")
        self._write_json(401, envelope(code="UNAUTHORIZED",
                                        message="missing or invalid X-Api-Key"))

    def _not_found(self, path: str) -> None:
        self._log_business(path, "NOT_FOUND")
        self._write_json(200, envelope(code="NOT_FOUND",
                                        message=f"unknown endpoint: {path}"))

    # -- resource pagination -----------------------------------------------------
    def _paginated_resource(self, path: str, resource: str, query: dict) -> None:
        rows = self.DATA.get(resource, [])
        offset = clamp_offset(query.get("offset", ["0"])[0])
        limit = clamp_limit(query.get("limit", [str(DEFAULT_LIMIT)])[0])
        total = len(rows)

        if _is_throttled():
            self._log_business(path, "RATE_LIMITED")
            self._write_json(200, envelope(
                code="RATE_LIMITED", message="rate limit exceeded, retry",
                total=total, returned=0, offset=offset,
                has_more=offset < total, truncated=False,
            ))
            return

        chunk = rows[offset: offset + limit]
        returned = len(chunk)
        has_more = (offset + returned) < total
        self._log_business(path, "OK")
        self._write_json(200, envelope(
            code="OK", total=total, returned=returned, offset=offset,
            has_more=has_more, truncated=False, data=chunk,
        ))

    def _holders(self, path: str) -> None:
        # "small enough not to paginate" -- always the full directory.
        rows = self.DATA.get("holders", [])
        if _is_throttled():
            self._log_business(path, "RATE_LIMITED")
            self._write_json(200, envelope(
                code="RATE_LIMITED", message="rate limit exceeded, retry",
                total=len(rows), returned=0, offset=0, has_more=False,
            ))
            return
        self._log_business(path, "OK")
        self._write_json(200, envelope(
            code="OK", total=len(rows), returned=len(rows), offset=0,
            has_more=False, truncated=False, data=rows,
        ))

    def _certificates_batch(self, path: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._log_business(path, "BAD_REQUEST")
            self._write_json(200, envelope(code="BAD_REQUEST",
                                            message="invalid JSON body"))
            return

        ids = body.get("ids") if isinstance(body, dict) else None
        if not isinstance(ids, list):
            self._log_business(path, "BAD_REQUEST")
            self._write_json(200, envelope(code="BAD_REQUEST",
                                            message="'ids' must be a list"))
            return

        if _is_throttled():
            self._log_business(path, "RATE_LIMITED")
            self._write_json(200, envelope(
                code="RATE_LIMITED", message="rate limit exceeded, retry",
                total=len(ids), returned=0,
            ))
            return

        truncated = len(ids) > BATCH_MAX
        # Above 25: first 25 ids in request order, self-describing via
        # `truncated: true` (J4) -- never an error, HTTP 200 either way.
        wanted = ids[:BATCH_MAX]
        pool = {c["certificate_id"]: c for c in self.DATA.get("certificates", [])}
        found = [pool[i] for i in wanted if i in pool]

        self._log_business(path, "OK")
        self._write_json(200, envelope(
            code="OK", total=len(ids), returned=len(found), offset=0,
            has_more=False, truncated=truncated, data=found,
        ))

    # -- HTTP verbs ------------------------------------------------------------------
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/healthz":
            # Never throttled, never authenticated.
            self._write_json(200, {"code": "OK"})
            return

        if not self._authorized():
            self._unauthorized(path)
            return

        if path in ("/v1/certificates", "/v1/option_grants", "/v1/awards"):
            resource = path.rsplit("/", 1)[-1]
            query = parse_qs(parsed.query)
            self._paginated_resource(path, resource, query)
        elif path == "/v1/holders":
            self._holders(path)
        else:
            self._not_found(path)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if not self._authorized():
            self._unauthorized(path)
            return

        if path == "/v1/certificates/batch":
            self._certificates_batch(path)
        else:
            self._not_found(path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", default="/opt/ledgerline/snapshot")
    ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    snapshot_dir = Path(args.snapshot)
    if not snapshot_dir.exists():
        sys.exit(f"snapshot dir not found: {snapshot_dir}")

    LedgerlineHandler.DATA = load_snapshot(snapshot_dir)
    LedgerlineHandler.API_KEY = resolve_api_key()

    server = ThreadingHTTPServer((args.host, args.port), LedgerlineHandler)
    print(f"[ledgerline] snapshot={snapshot_dir} host={args.host} port={args.port}",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
