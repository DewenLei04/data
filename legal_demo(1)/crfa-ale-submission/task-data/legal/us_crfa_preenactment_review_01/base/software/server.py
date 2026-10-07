#!/usr/bin/env python3
"""UniUSLaw — U.S. law search site (fictional commercial database).

Fictional commercial legal database "UniUSLaw (uniuslaw.com)": seven annual U.S.C. editions
+ current C.F.R. + federal case law (SCOTUS and the 13 circuits). Content is real (public
resources); it reuses no name, domain, or visuals of any official or commercial platform
(Westlaw/Lexis/CourtListener/Justia). Zero third-party dependencies (stdlib http.server + sqlite3).

Routes:
    /                          home (universal search + data overview)
    /search?q=...              unified search: exact citation match, else statute + case columns
    /usc/<title>/<sec>         U.S.C. section page (?edition=2018..2024 version switch)
    /cfr/<title>/<sec>         C.F.R. section page (current)
    /cases?q=&court=&from=&to= case search (FTS full text / case name / citation + court/date filters)
    /case/<cluster_id>         case detail (opinion layering + citing/cited + parentheticals)
    /cite/<citation>           universal citation resolver (302 → detail page)
    /stats                     data overview (live statistics)

Dual channel: every route serves HTML by default; `?format=json` or Accept: application/json returns JSON.

Friction switches (environment variables, all off by default):
    FRICTION_RATE_LIMIT=7   return a RATE_LIMITED page every N requests (HTTP 200, cursor does not advance)
    FRICTION_PAGINATION=20  items per page
    FRICTION_NO_JSON=1      disable the JSON channel
    FRICTION_SNIPPET_ONLY=1 list views show snippets only
    ACCESS_LOG=<path>       per-request jsonl log

Usage:
    python3 server.py --port 8080
    python3 server.py --selftest
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

SITE_NAME = "UniUSLaw"
SITE_DOMAIN = os.environ.get("SITE_HOST", "uniuslaw.com")
SITE_TAGLINE = "U.S. Code · Code of Federal Regulations · Federal Case Law"
DB_PATH = os.environ.get(
    "UNIUSLAW_DB",
    "data/us_law.db")


def find_db() -> str:
    """DB location: --db > UNIUSLAW_DB > bundled data/us_law.db > source-tree default.

    The release bundle (server.py beside data/us_law.db) needs no machine-specific absolute path."""
    here = Path(__file__).resolve().parent
    for cand in (os.environ.get("UNIUSLAW_DB", ""),
                 str(here / "data" / "us_law.db"),
                 "data/us_law.db"):
        if cand and Path(cand).is_file():
            return cand
    return "data/us_law.db"

USC_EDITIONS = (2018, 2019, 2020, 2021, 2022, 2023, 2024)
CURRENT_EDITION = 2024

COURTS = {
    "scotus": "Supreme Court of the United States",
    "cadc": "D.C. Circuit", "ca1": "First Circuit", "ca2": "Second Circuit",
    "ca3": "Third Circuit", "ca4": "Fourth Circuit", "ca5": "Fifth Circuit",
    "ca6": "Sixth Circuit", "ca7": "Seventh Circuit", "ca8": "Eighth Circuit",
    "ca9": "Ninth Circuit", "ca10": "Tenth Circuit", "ca11": "Eleventh Circuit",
    "cafc": "Federal Circuit",
}

OPINION_LABELS = [
    ("010combined", "Opinion of the Court"),
    ("020lead", "Lead Opinion"),
    ("025plurality", "Plurality Opinion"),
    ("030concurrence", "Concurring Opinion"),
    ("035concurrenceinpart", "Concurring in Part"),
    ("040dissent", "Dissenting Opinion"),
    ("060remittitur", "Remittitur"),
    ("070rehearing", "On Rehearing"),
    ("080onthemerits", "On the Merits"),
]

CITATION_RE = re.compile(
    r"^\s*(?:"
    r"(?P<usc_t>\d{1,2})\s+U\.?\s?S\.?\s?C\.?\s*[§]?\s*(?P<usc_s>[\w–\-]+)"
    r"|(?P<cfr_t>\d{1,2})\s+C\.?\s?F\.?\s?R\.?\s*[§]?\s*(?P<cfr_s>[\w.\-]+)"
    r"|(?P<vol>\d{1,4})\s+(?P<rep>[A-Za-z][A-Za-z0-9 .]{0,30}?)\s+(?P<page>\d{1,5})"
    r")\s*$", re.IGNORECASE)


@dataclass
class Friction:
    rate_limit: int = 0
    pagination: int = 20
    no_json: bool = False
    snippet_only: bool = False
    access_log: str = ""

    @classmethod
    def from_env(cls) -> "Friction":
        return cls(
            rate_limit=int(os.environ.get("FRICTION_RATE_LIMIT", "0") or 0),
            pagination=int(os.environ.get("FRICTION_PAGINATION", "20") or 20),
            no_json=os.environ.get("FRICTION_NO_JSON", "") not in ("", "0"),
            snippet_only=os.environ.get("FRICTION_SNIPPET_ONLY", "") not in ("", "0"),
            access_log=os.environ.get("ACCESS_LOG", ""),
        )

    def describe(self) -> str:
        on = []
        if self.rate_limit:
            on.append(f"rate-limit 1/{self.rate_limit}")
        if self.no_json:
            on.append("HTML only")
        if self.snippet_only:
            on.append("snippet only")
        if self.access_log:
            on.append("access log")
        on.append(f"{self.pagination}/page")
        return " · ".join(on)


# --------------------------------------------------------------------------
# Data access
# --------------------------------------------------------------------------

_local = threading.local()


def db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=1")
        _local.conn = conn
    return conn


def fts_escape(q: str) -> str:
    """User input → safe FTS5 query: double-quote each word and join with AND (prevents syntax injection)."""
    words = [w.replace('"', "") for w in q.split() if w.strip()]
    return " AND ".join(f'"{w}"' for w in words[:12]) if words else ""


def int_or(s, default: int) -> int:
    """Safe integer parsing: bad input falls back to default (prevents page=abc-style 500s)."""
    try:
        return int(s)
    except (ValueError, TypeError):
        return default


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(s: str) -> str:
    if not s:
        return ""
    import html as _h
    return _WS_RE.sub(" ", _h.unescape(_TAG_RE.sub(" ", s))).strip()


def op_label(t: str) -> str:
    for prefix, label in OPINION_LABELS:
        if t.startswith(prefix):
            return label
    return "Opinion"


# --------------------------------------------------------------------------
# Citation resolution
# --------------------------------------------------------------------------

def classify_citation(q: str) -> dict | None:
    """Detect whether the user input is a standard citation (USC/CFR/case)."""
    q = q.strip().replace("§ ", "§").replace("  ", " ")
    m = CITATION_RE.match(q)
    if not m:
        return None
    if m.group("usc_t"):
        return {"kind": "usc", "title": m.group("usc_t"), "sec": m.group("usc_s")}
    if m.group("cfr_t"):
        return {"kind": "cfr", "title": m.group("cfr_t"), "sec": m.group("cfr_s")}
    return {"kind": "case", "citation": q.strip()}


def resolve_citation(cit: str) -> dict | None:
    norm = re.sub(r"\s+", " ", cit.strip())  # collapse consecutive spaces
    row = db().execute(
        "SELECT citation, ref_type, ref_id, display_name, status "
        "FROM citation_index WHERE citation = ? COLLATE NOCASE",
        (norm,)).fetchone()
    if row:
        return dict(row)
    # fallback: dot-stripped match (covers "347 US 483" dotless shorthand); REPLACE
    # bypasses the index but fires only after exact match fails — ~1s full scan is fine
    row = db().execute(
        "SELECT citation, ref_type, ref_id, display_name, status "
        "FROM citation_index WHERE REPLACE(citation, '.', '') = ? "
        "COLLATE NOCASE LIMIT 1",
        (norm.replace(".", ""),)).fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------
# Query implementation
# --------------------------------------------------------------------------

def search_statutes(q: str, kind: str, page: int, per: int) -> dict:
    """Statute search: FTS full text. kind: all|usc|cfr|usc-2018..2024."""
    match = fts_escape(q)
    if not match:
        return {"rows": [], "total": 0}
    srcs = []
    if kind in ("all", "cfr"):
        srcs.append("'cfr'")
    if kind == "all" or kind == "usc" or (kind.startswith("usc-")):
        if kind.startswith("usc-"):
            srcs.append(f"'usc/{kind[4:]}'")  # usc-2018 → usc/2018
        else:
            srcs.append("'usc/2024'")
    src_in = ",".join(srcs)
    base = (f"FROM statute_fts WHERE statute_fts MATCH ? "
            f"AND source IN ({src_in})")
    total = db().execute(
        f"SELECT COUNT(*) {base}", (match,)).fetchone()[0]
    rows = db().execute(
        f"SELECT citation, source, ref_id, heading, "
        f"snippet(statute_fts, 4, '»', '«', ' … ', 18) AS snip {base} "
        f"ORDER BY rank LIMIT ? OFFSET ?",
        (match, per, (page - 1) * per)).fetchall()
    return {"total": total,
            "rows": [dict(r) for r in rows]}


FTS_TOP = 5000  # FTS hit truncation cap (opinion rows before dedup)


def search_cases(q: str, court: str, date_from: str, date_to: str,
                 mode: str, page: int, per: int, sort: str = "relevance") -> dict:
    """Case search. mode: fts (full text)|name (case name); sort: relevance|date.

    Two-phase FTS: first take the top FTS_TOP opinion rows by rank (dedup by
    cluster), then JOIN metadata for filter/sort/pagination. When hits reach
    the cap, total is marked with a truncation flag.
    """
    where, args = [], []
    if mode == "name" and q:
        where.append("k.case_name LIKE ?")
        args.append(f"%{q}%")
    if court:
        where.append("d.court_id = ?")
        args.append(court)
    if date_from:
        # date_filed is DATE (NUMERIC affinity); a bare-number argument like '2010' triggers
        # a TEXT vs NUMERIC mixed comparison (SQLite: TEXT always > NUMERIC), breaking the filter.
        # CAST to TEXT forces string comparison — correct for both '2010' and '2010-01-01'.
        where.append("CAST(k.date_filed AS TEXT) >= ?")
        args.append(date_from)
    if date_to:
        where.append("CAST(k.date_filed AS TEXT) <= ?")
        args.append(date_to)
    w = ("WHERE " + " AND ".join(where)) if where else ""

    if mode == "fts" and q:
        match = fts_escape(q)
        if not match:
            return {"total": 0, "truncated": False, "rows": []}
        # Single query: FTS top-FTS_TOP → JOIN metadata → GROUP BY dedup,
        # sort + paginate on the Python side (avoids the 60s full-count aggregate)
        fargs = [match] + args
        try:
            allrows = db().execute(
                f"SELECT k.id, k.case_name, k.date_filed, d.court_id, "
                f"(SELECT GROUP_CONCAT(volume||' '||reporter||' '||page) "
                f"FROM cl_citations WHERE cluster_id=k.id) AS cites, "
                f"MIN(f.rk) AS best_rk, MIN(f.snip) AS snippet "
                f"FROM (SELECT cluster_id, rank AS rk, snippet(case_fts, 4,"
                f" '»', '«', ' … ', 18) AS snip FROM case_fts "
                f"WHERE case_fts MATCH ? "
                f"ORDER BY rank LIMIT {FTS_TOP}) f "
                f"JOIN cl_clusters k ON k.id = f.cluster_id "
                f"JOIN cl_dockets d ON k.docket_id = d.id {w} "
                f"GROUP BY k.id", fargs).fetchall()
        except sqlite3.OperationalError:
            allrows = []
        if sort == "date":
            allrows = sorted(
                allrows, key=lambda r: (r["date_filed"] is not None,
                                        r["date_filed"] or ""), reverse=True)
        else:
            allrows = sorted(allrows, key=lambda r: r["best_rk"])
        n_f = db().execute(
            f"SELECT COUNT(*) FROM (SELECT 1 FROM case_fts "
            f"WHERE case_fts MATCH ? LIMIT {FTS_TOP + 1})", (match,)).fetchone()[0]
        truncated = n_f > FTS_TOP
        start = (page - 1) * per
        return {"total": len(allrows), "truncated": truncated,
                "rows": [dict(r) for r in allrows[start:start + per]]}

    rows = db().execute(
        f"SELECT k.id, k.case_name, k.date_filed, d.court_id, "
        f"(SELECT GROUP_CONCAT(volume||' '||reporter||' '||page) "
        f"FROM cl_citations WHERE cluster_id=k.id) AS cites "
        f"FROM cl_clusters k JOIN cl_dockets d ON k.docket_id=d.id {w} "
        f"ORDER BY k.date_filed DESC LIMIT ? OFFSET ?",
        args + [per, (page - 1) * per]).fetchall()
    total = db().execute(
        f"SELECT COUNT(*) FROM cl_clusters k JOIN cl_dockets d "
        f"ON k.docket_id=d.id {w}", args).fetchone()[0]
    return {"total": total, "truncated": False, "rows": [dict(r) for r in rows]}


def usc_section(title: str, sec: str, edition: int) -> dict | None:
    # Compound section numbers (e.g. §285a-1) are stored with an en dash (–) while
    # URL input usually uses hyphen (-); normalize hyphen→en dash or 5222 compound sections can never be found
    sec = sec.replace("-", "–")
    row = db().execute(
        "SELECT * FROM usc_sections WHERE title_number=? AND section_number=? "
        "AND edition_year=? AND appendix_flag=0",
        (int(title), sec, edition)).fetchone()
    if not row:
        return None
    import hashlib
    d = dict(row)
    d["versions"] = []
    for v in db().execute(
            "SELECT edition_year, heading, body_text FROM usc_sections "
            "WHERE title_number=? AND section_number=? AND appendix_flag=0 "
            "ORDER BY edition_year", (int(title), sec)).fetchall():
        d["versions"].append({
            "edition_year": v["edition_year"], "heading": v["heading"],
            "len": len(v["body_text"] or ""),
            "hash": hashlib.md5((v["body_text"] or "").encode()).hexdigest()})
    return d


def cfr_section(title: str, sec: str) -> dict | None:
    row = db().execute(
        "SELECT * FROM cfr_sections WHERE title_number=? AND identifier=? "
        "AND body_text != '' ORDER BY snapshot_date DESC LIMIT 1",
        (int(title), sec)).fetchone()
    if not row:
        row = db().execute(
            "SELECT * FROM cfr_sections WHERE title_number=? AND identifier=? "
            "ORDER BY snapshot_date DESC LIMIT 1", (int(title), sec)).fetchone()
    return dict(row) if row else None


def case_detail(cluster_id: str) -> dict | None:
    row = db().execute(
        "SELECT k.*, d.court_id, d.docket_number, d.cause "
        "FROM cl_clusters k JOIN cl_dockets d ON k.docket_id=d.id "
        "WHERE k.id=?", (cluster_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["court_name"] = COURTS.get(d.get("court_id"), d.get("court_id") or "")
    d["cites"] = [dict(c) for c in db().execute(
        "SELECT volume, reporter, page FROM cl_citations "
        "WHERE cluster_id=?", (cluster_id,)).fetchall()]
    d["cites_str"] = "; ".join(
        f'{c["volume"]} {c["reporter"]} {c["page"]}' for c in d["cites"])
    d["opinions"] = [dict(o) for o in db().execute(
        "SELECT id, type, per_curiam, html_with_citations, plain_text "
        "FROM cl_opinions WHERE cluster_id=? ORDER BY ordering_key, id",
        (cluster_id,)).fetchall()]
    for o in d["opinions"]:
        o["text"] = strip_html(o.get("html_with_citations")
                               or o.get("plain_text") or "")
        o["label"] = op_label(o.get("type") or "")
    return d


def citing_references(cluster_id: str, limit: int, offset: int,
                      q: str = "", court: str = "") -> dict:
    """Cases citing this case (with parentheticals). q matches citing case name or parenthetical text; court filters by court."""
    filt, fargs = [], []
    if q:
        filt.append("(k2.case_name LIKE ? OR EXISTS ("
                    "SELECT 1 FROM cl_parentheticals p "
                    "JOIN cl_opinions od ON p.described_opinion_id=od.id "
                    "WHERE od.cluster_id=? AND p.describing_opinion_id="
                    "oc.citing_opinion_id AND p.text LIKE ?))")
        fargs += [f"%{q}%", cluster_id, f"%{q}%"]
    if court:
        filt.append("d2.court_id = ?")
        fargs.append(court)
    fw = ("AND " + " AND ".join(filt)) if filt else ""
    total = db().execute(
        f"SELECT COUNT(DISTINCT oc.citing_opinion_id) FROM cl_opinions_cited oc "
        f"JOIN cl_opinions o ON oc.cited_opinion_id=o.id "
        f"JOIN cl_opinions o2 ON oc.citing_opinion_id=o2.id "
        f"JOIN cl_clusters k2 ON o2.cluster_id=k2.id "
        f"JOIN cl_dockets d2 ON k2.docket_id=d2.id "
        f"WHERE o.cluster_id=? {fw}", [cluster_id] + fargs).fetchone()[0]
    rows = db().execute(
        f"SELECT DISTINCT k2.id AS cid, k2.case_name, k2.date_filed, "
        f"d2.court_id, (SELECT GROUP_CONCAT(volume||' '||reporter||' '||page) "
        f"FROM cl_citations WHERE cluster_id=k2.id) AS cites, "
        f"(SELECT p.text FROM cl_parentheticals p "
        f"JOIN cl_opinions od ON p.described_opinion_id=od.id "
        f"WHERE od.cluster_id=? AND p.describing_opinion_id=oc.citing_opinion_id "
        f"LIMIT 1) AS paren "
        f"FROM cl_opinions_cited oc "
        f"JOIN cl_opinions o ON oc.cited_opinion_id=o.id "
        f"JOIN cl_opinions o2 ON oc.citing_opinion_id=o2.id "
        f"JOIN cl_clusters k2 ON o2.cluster_id=k2.id "
        f"JOIN cl_dockets d2 ON k2.docket_id=d2.id "
        f"WHERE o.cluster_id=? {fw} ORDER BY k2.date_filed DESC "
        f"LIMIT ? OFFSET ?",
        [cluster_id, cluster_id] + fargs + [limit, offset]
    ).fetchall()
    return {"total": total, "rows": [dict(r) for r in rows]}


def cited_authorities(cluster_id: str, limit: int) -> list[dict]:
    rows = db().execute(
        "SELECT k2.id AS cid, k2.case_name, k2.date_filed, oc.depth, "
        "(SELECT GROUP_CONCAT(volume||' '||reporter||' '||page) "
        "FROM cl_citations WHERE cluster_id=k2.id) AS cites "
        "FROM cl_opinions o "
        "JOIN cl_opinions_cited oc ON oc.citing_opinion_id=o.id "
        "JOIN cl_opinions o2 ON oc.cited_opinion_id=o2.id "
        "JOIN cl_clusters k2 ON o2.cluster_id=k2.id "
        "WHERE o.cluster_id=? GROUP BY k2.id "
        "ORDER BY oc.depth DESC, k2.date_filed DESC LIMIT ?",
        (cluster_id, limit)).fetchall()
    return [dict(r) for r in rows]


def usc_title_browse(title: str, edition: int) -> dict | None:
    """USC Title catalog: chapter list (with section counts) + title name."""
    t = int(title)
    trow = db().execute(
        "SELECT name FROM usc_titles WHERE title_number=?", (t,)).fetchone()
    if not trow:
        return None
    chapters = [dict(r) for r in db().execute(
        "SELECT c.chapter, c.name, "
        "(SELECT COUNT(*) FROM usc_sections s WHERE s.title_number=c.title_number "
        "AND s.edition_year=c.edition_year AND s.chapter=c.chapter) AS n "
        "FROM usc_chapters c WHERE c.title_number=? AND c.edition_year=? "
        "ORDER BY CAST(c.chapter AS INTEGER)", (t, edition)).fetchall()]
    unchapt = db().execute(
        "SELECT COUNT(*) FROM usc_sections WHERE title_number=? "
        "AND edition_year=? AND (chapter IS NULL OR chapter='')",
        (t, edition)).fetchone()[0]
    total = db().execute(
        "SELECT COUNT(*) FROM usc_sections WHERE title_number=? "
        "AND edition_year=?", (t, edition)).fetchone()[0]
    return {"title": t, "name": trow["name"], "edition": edition,
            "chapters": chapters, "unchaptered": unchapt, "total": total}


def usc_chapter_browse(title: str, chapter: str, edition: int) -> dict | None:
    """Section list within a USC chapter."""
    t = int(title)
    crow = db().execute(
        "SELECT name FROM usc_chapters WHERE title_number=? AND chapter=? "
        "AND edition_year=?", (t, chapter, edition)).fetchone()
    rows = [dict(r) for r in db().execute(
        "SELECT section_number, heading, length(body_text) AS len "
        "FROM usc_sections WHERE title_number=? AND edition_year=? "
        "AND chapter=? AND appendix_flag=0 ORDER BY rowid",
        (t, edition, chapter)).fetchall()]
    if not crow and not rows:
        return None
    return {"title": t, "chapter": chapter,
            "name": crow["name"] if crow else "", "sections": rows}


def cfr_title_browse(title: str) -> dict | None:
    """CFR Title catalog: part list (from cfr_structure)."""
    t = int(title)
    trow = db().execute(
        "SELECT name FROM cfr_titles WHERE number=?", (t,)).fetchone()
    if not trow:
        return None
    parts = [dict(r) for r in db().execute(
        "SELECT identifier, label, reserved FROM cfr_structure "
        "WHERE title_number=? AND type='part' AND snapshot_date="
        "(SELECT snapshot_date FROM cfr_structure WHERE title_number=? "
        " GROUP BY snapshot_date ORDER BY COUNT(*) DESC LIMIT 1) "
        "ORDER BY CAST(identifier AS REAL)", (t, t)).fetchall()]
    n = db().execute(
        "SELECT COUNT(*) FROM cfr_sections WHERE title_number=? "
        "AND body_text != ''", (t,)).fetchone()[0]
    return {"title": t, "name": trow["name"], "parts": parts, "sections": n}


def cfr_part_sections(title: str, part: str) -> dict | None:
    """CFR part detail: section list under that part."""
    t = int(title)
    prow = db().execute(
        "SELECT identifier, label FROM cfr_structure WHERE title_number=? "
        "AND type='part' AND identifier=? AND snapshot_date="
        "(SELECT snapshot_date FROM cfr_structure WHERE title_number=? "
        " GROUP BY snapshot_date ORDER BY COUNT(*) DESC LIMIT 1)",
        (t, part, t)).fetchone()
    rows = [dict(r) for r in db().execute(
        "SELECT identifier, name, length(body_text) AS len "
        "FROM cfr_sections WHERE title_number=? AND part=? AND body_text != '' "
        "ORDER BY CAST(identifier AS REAL)", (t, part)).fetchall()]
    if not prow and not rows:
        return None
    return {"title": t, "part": part,
            "name": prow["label"] if prow else "", "sections": rows}


# --------------------------------------------------------------------------
# HTML rendering
# --------------------------------------------------------------------------

CSS = """
:root{--ink:#14212e;--mut:#5b6b7a;--line:#dde4ea;--acc:#1a4d8f;--bg:#f7f9fb}
*{box-sizing:border-box;margin:0}
body{font:15px/1.55 Georgia,'Times New Roman',serif;color:var(--ink);
     background:var(--bg)}
header{background:#fff;border-bottom:1px solid var(--line);padding:14px 28px;
       display:flex;align-items:baseline;gap:18px}
header .brand{font-size:21px;font-weight:700;color:var(--acc);
     letter-spacing:.4px}
header .tag{color:var(--mut);font-size:13px}
header nav{margin-left:auto;font-family:Verdana,sans-serif;font-size:13px}
header nav a{color:var(--ink);text-decoration:none;margin-left:16px}
main{max-width:1060px;margin:26px auto;padding:0 24px}
h1{font-size:24px;margin-bottom:6px}
h2{font-size:17px;margin:26px 0 10px;color:var(--acc);
   border-bottom:1px solid var(--line);padding-bottom:5px}
.mut{color:var(--mut);font-size:13px}
a{color:var(--acc)}
input[type=text],select{font:14px Verdana,sans-serif;padding:8px 10px;
     border:1px solid var(--line);border-radius:3px;background:#fff}
.bigbox{width:640px;max-width:100%}
button{font:14px Verdana,sans-serif;padding:9px 20px;background:var(--acc);
     color:#fff;border:0;border-radius:3px;cursor:pointer}
.card{background:#fff;border:1px solid var(--line);border-radius:4px;
     padding:18px 22px;margin-bottom:14px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{font-family:Verdana,sans-serif;font-size:12px;color:var(--mut);
   text-align:left;border-bottom:1px solid var(--line);padding:6px 8px}
td{border-bottom:1px solid var(--line);padding:7px 8px;vertical-align:top}
.hit{background:#fff8dd}
.pager{margin:18px 0;font-family:Verdana,sans-serif;font-size:13px}
.pager a,.pager span{margin-right:8px;padding:4px 9px;border:1px solid var(--line);
   border-radius:3px;text-decoration:none;background:#fff}
.pager .cur{background:var(--acc);color:#fff;border-color:var(--acc)}
.stat-grid{display:flex;gap:14px;flex-wrap:wrap;margin-top:16px}
.stat{background:#fff;border:1px solid var(--line);border-radius:4px;
      padding:14px 20px;min-width:150px}
.stat b{display:block;font-size:22px;color:var(--acc)}
.stat span{font-size:12px;color:var(--mut);font-family:Verdana,sans-serif}
.editions a,.editions .cur{margin-right:8px;padding:3px 10px;border:1px solid var(--line);
   border-radius:3px;text-decoration:none;font-family:Verdana,sans-serif;
   font-size:12px;background:#fff}
.editions .cur{background:var(--acc);color:#fff;border-color:var(--acc)}
.lawtext{white-space:pre-wrap;font-size:15.5px;margin-top:14px;
   line-height:1.75;background:#fff}
footer{margin:44px auto;padding:18px 24px;max-width:1060px;color:var(--mut);
   font-size:12px;border-top:1px solid var(--line);
   font-family:Verdana,sans-serif}
.snippet{font-size:13px;color:#333;margin-top:2px}
.opsel{font-family:Verdana,sans-serif;font-size:13px;font-weight:700;
   color:var(--acc);margin:22px 0 4px}
.paren{color:#444;font-size:13.5px;margin-top:2px}
.ins{background:#e2f4e2;text-decoration:none}
.del{background:#fde3e1;text-decoration:line-through}
.filterbar{background:#fff;border:1px solid var(--line);border-radius:4px;
   padding:10px 14px;margin:12px 0;font-size:13px}
.filterbar input[type=text]{width:220px}
.flag{background:#fdecea;color:#a12622;border:1px solid #f3c1bd;
   border-radius:3px;padding:2px 8px;font-size:12px;font-family:Verdana,sans-serif}
.note{background:#f2f6fb;border:1px solid #d7e3f0;border-radius:4px;
   padding:10px 14px;font-size:13px;color:#31506e;margin:12px 0}
"""


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def page(title: str, body: str, q: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{esc(title)} — {SITE_NAME}</title>
<style>{CSS}</style></head><body>
<header>
 <a class="brand" href="/" style="text-decoration:none">{SITE_NAME}</a>
 <span class="tag">{SITE_TAGLINE}</span>
 <nav><a href="/search">Search</a><a href="/cases">Cases</a>
      <a href="/stats">About the Data</a></nav>
</header>
<main>{body}</main>
<footer>{SITE_NAME} ({SITE_DOMAIN}) · Contents compiled from public resources
(U.S. Government Publishing Office; federal courts).
Data current through August 2025.
</footer></body></html>"""


def home(q: str) -> str:
    stats = quick_stats()
    body = f"""
<h1>Search U.S. Law</h1>
<p class="mut">Enter a citation (e.g. <i>17 U.S.C. § 107</i>,
<i>21 CFR § 1.1</i>, <i>347 U.S. 483</i>), a case name, or keywords.</p>
<form action="/search" method="get" style="margin:18px 0 6px">
 <input class="bigbox" type="text" name="q" value="{esc(q)}" autofocus
        placeholder="citation, case name, or full-text keywords">
 <button type="submit">Search</button>
</form>
<div class="stat-grid">
 <div class="stat"><b>{stats['usc']:,}</b><span>U.S. Code sections · 7 editions</span></div>
 <div class="stat"><b>{stats['cfr']:,}</b><span>CFR sections (current)</span></div>
 <div class="stat"><b>{stats['opinions']:,}</b><span>federal opinions</span></div>
 <div class="stat"><b>{stats['cites']:,}</b><span>citation links</span></div>
</div>
<h2>Browse</h2>
<div class="card">
 <p><a href="/cases">Case Law Search</a> — full text of
 {stats['opinions']:,} opinions, Supreme Court and 13 circuits,
 with citing references.</p>
 <p><a href="/search?q=copyright+fair+use&amp;kind=usc">U.S. Code</a> —
 current text plus every annual edition {USC_EDITIONS[0]}–{CURRENT_EDITION}.</p>
 <p><a href="/search?q=endangered+species&amp;kind=cfr">Code of Federal
 Regulations</a> — current snapshot.</p>
</div>"""
    return page("Search", body)


def quick_stats() -> dict:
    c = db()
    return {
        "usc": c.execute("SELECT COUNT(*) FROM usc_sections "
                         "WHERE edition_year=?", (CURRENT_EDITION,)).fetchone()[0],
        "cfr": c.execute("SELECT COUNT(*) FROM cfr_sections "
                         "WHERE body_text != ''").fetchone()[0],
        "opinions": 1994970,
        "cites": 11917570,
    }


def render_usc_browse(b: dict | None, title: str, edition: int) -> str:
    if not b:
        return page("Not found", f"<h1>Title {esc(title)} not found</h1>")
    rows = "".join(
        f'<tr><td><a href="/usc/{b["title"]}/ch/{esc(c["chapter"])}">'
        f'Chapter {esc(c["chapter"])}</a></td>'
        f'<td>{esc(c["name"] or "")}</td><td>{c["n"]}</td></tr>'
        for c in b["chapters"])
    unch = (f'<tr><td></td><td class="mut">Unclassified sections</td>'
            f'<td>{b["unchaptered"]}</td></tr>') if b["unchaptered"] else ""
    eds = " ".join(
        f'<a href="/usc/{b["title"]}?edition={e}">{e}</a>'
        if e != edition else f'<span class="cur">{e}</span>'
        for e in USC_EDITIONS)
    body = f"""
<h1>{b["title"]} U.S.C. — {esc(b["name"])}</h1>
<p class="mut">{b["total"]:,} sections in edition {edition}.
 View edition: {eds}</p>
<h2>Chapters</h2>
<table><tr><th>Chapter</th><th>Name</th><th>Sections</th></tr>{rows}{unch}</table>"""
    return page(f"Title {b['title']} U.S.C.", body)


def render_usc_chapter(b: dict | None, title: str, chapter: str,
                       edition: int) -> str:
    if not b:
        return page("Not found",
                    f"<h1>Chapter not found</h1><p>Title {esc(title)} "
                    f"chapter {esc(chapter)} (edition {edition}).</p>")
    rows = "".join(
        f'<tr><td><a href="/usc/{b["title"]}/{esc(s["section_number"])}'
        f'?edition={edition}">§ {esc(s["section_number"])}</a></td>'
        f'<td>{esc(s["heading"] or "")}</td><td>{s["len"]:,}</td></tr>'
        for s in b["sections"])
    body = f"""
<h1>{b["title"]} U.S.C. — Chapter {esc(b["chapter"])}
{(" — " + esc(b["name"])) if b["name"] else ""}</h1>
<p class="mut">{len(b["sections"])} sections · edition {edition} ·
 <a href="/usc/{b["title"]}?edition={edition}">← Title {b["title"]}</a></p>
<table><tr><th>Section</th><th>Heading</th><th>Chars</th></tr>{rows}</table>"""
    return page(f"{b['title']} USC ch. {b['chapter']}", body)


def render_cfr_browse(b: dict | None, title: str) -> str:
    if not b:
        return page("Not found", f"<h1>Title {esc(title)} C.F.R. not found</h1>")
    rows = "".join(
        f'<tr><td><a href="/cfr/{b["title"]}/p/{esc(p["identifier"])}">'
        f'Part {esc(p["identifier"])}</a></td>'
        f'<td>{esc(p["label"] or "")}</td>'
        f'<td>{"reserved" if p["reserved"] else ""}</td></tr>'
        for p in b["parts"])
    body = f"""
<h1>{b["title"]} C.F.R. — {esc(b["name"])}</h1>
<p class="mut">{b["sections"]:,} sections with text (current snapshot).</p>
<h2>Parts</h2>
<table><tr><th>Part</th><th>Name</th><th></th></tr>{rows}</table>"""
    return page(f"Title {b['title']} CFR", body)


def render_cfr_part(b: dict | None, title: str, part: str) -> str:
    if not b:
        return page("Not found",
                    f"<h1>Part not found</h1><p>No CFR title {esc(title)} "
                    f"part {esc(part)} in the database.</p>")
    rows = "".join(
        f'<tr><td><a href="/cfr/{b["title"]}/{esc(s["identifier"])}">'
        f'§ {esc(s["identifier"])}</a></td>'
        f'<td>{esc(s["name"] or "")}</td><td>{s["len"]:,}</td></tr>'
        for s in b["sections"])
    body = f"""
<h1>{b["title"]} C.F.R. — Part {esc(b["part"])}
{(" — " + esc(b["name"])) if b["name"] else ""}</h1>
<p class="mut">{len(b["sections"])} sections with text ·
 <a href="/cfr/{b["title"]}">← Title {b["title"]}</a></p>
<table><tr><th>Section</th><th>Name</th><th>Chars</th></tr>{rows}</table>"""
    return page(f"{b['title']} CFR Part {b['part']}", body)


def render_search(q: str, kind: str, results: dict, case_res: dict,
                  resolved: dict | None, page_no: int, per: int) -> str:
    body = [f"""<h1>{'Search: ' + esc(q) if q else 'Search U.S. Law'}</h1>
<form action="/search" method="get" style="margin-bottom:14px">
 <input class="bigbox" type="text" name="q" value="{esc(q)}">
 <select name="kind">
  <option value="all"{' selected' if kind == 'all' else ''}>All statutes</option>
  <option value="usc"{' selected' if kind == 'usc' else ''}>U.S. Code (current)</option>"""]
    for e in USC_EDITIONS:
        body.append(f'<option value="usc-{e}"'
                    f'{" selected" if kind == f"usc-{e}" else ""}>'
                    f"U.S. Code {e} edition</option>")
    body.append(f"""
  <option value="cfr"{' selected' if kind == 'cfr' else ''}>CFR</option>
 </select>
 <button type="submit">Search</button>
</form>""")

    if resolved:
        if resolved["ref_type"] == "statute":
            parts = resolved["ref_id"].split("/")  # usc/2024/title17/chx/sec107
            t, s = parts[2][5:], parts[4][3:]      # '17', '107'
            body.append(f'<div class="note">Citation resolved → '
                        f'<a href="/usc/{t}/{s}">{esc(resolved["citation"])}</a>'
                        f' — {esc(resolved.get("display_name") or "")}</div>')
        elif resolved["ref_type"] == "regulation":
            parts = resolved["ref_id"].split("/")
            body.append(f'<div class="note">Citation resolved → '
                        f'<a href="/cfr/{parts[1]}/{parts[2]}">'
                        f'{esc(resolved["citation"])}</a></div>')
        elif resolved["ref_type"] == "case":
            body.append(f'<div class="note">Citation resolved → '
                        f'<a href="/case/{resolved["ref_id"]}">'
                        f'{esc(resolved["citation"])} — '
                        f'{esc(resolved.get("display_name") or "")}</a></div>')

    if q:
        n = results["total"]
        body.append(f'<h2>Statutes — {n:,} hit'
                    f'{"s" if n != 1 else ""}</h2>')
        if n:
            body.append('<table><tr><th>Citation</th><th>Source</th>'
                        '<th>Excerpt</th></tr>')
            for r in results["rows"]:
                body.append(
                    f'<tr><td><a href="/statute/{quote(r["ref_id"])}">'
                    f'{esc(r["citation"])}</a><br>'
                    f'<span class="mut">{esc(r["heading"] or "")}</span></td>'
                    f'<td>{esc(r["source"])}</td>'
                    f'<td class="snippet">{esc(r["snip"])}</td></tr>')
            body.append("</table>")
        cn = case_res["total"]
        body.append(f'<h2>Cases — {cn:,} cluster'
                    f'{"s" if cn != 1 else ""}</h2>')
        if cn:
            body.append('<table><tr><th>Case</th><th>Citation</th>'
                        '<th>Court</th><th>Date</th></tr>')
            for r in case_res["rows"][: per]:
                body.append(
                    f'<tr><td><a href="/case/{r["id"]}">'
                    f'{esc(r["case_name"])}</a>'
                    + (f'<div class="snippet">{esc(r.get("snippet",""))}</div>'
                       if r.get("snippet") else "") +
                    f'</td><td>{esc(r.get("cites") or "")}</td>'
                    f'<td>{esc(COURTS.get(r.get("court_id"), r.get("court_id") or ""))}</td>'
                    f'<td>{esc(r.get("date_filed") or "")}</td></tr>')
            body.append("</table>")
            if cn > per:
                body.append(f'<p class="mut">Showing first {per}. '
                            f'<a href="/cases?q={quote(q)}">Refine in case '
                            f'search →</a></p>')
    return page(f"Search: {q}" if q else "Search", "".join(body))


def render_usc(sec: dict, edition: int, err: str | None,
               diff_edition: int | None = None) -> str:
    if err:
        return page("Not found", f'<h1>Section not found</h1><p>{esc(err)}</p>')
    t, s = sec["title_number"], sec["section_number"]
    hashes = {v["edition_year"]: v["hash"] for v in sec["versions"]}
    eds = "".join(
        (f'<span class="cur">{v["edition_year"]}</span>'
         if v["edition_year"] == edition else
         f'<a href="/usc/{t}/{s}?edition={v["edition_year"]}">'
         f'{v["edition_year"]}</a>')
        + (" <sup>†</sup>"
           if v["edition_year"] in hashes
           and hashes[v["edition_year"]] != hashes.get(USC_EDITIONS[0])
           and v["edition_year"] != USC_EDITIONS[0] else "")
        for v in sec["versions"])
    compare = ""
    body_html = esc(sec["body_text"])
    if diff_edition and diff_edition != edition:
        other = db().execute(
            "SELECT body_text FROM usc_sections WHERE title_number=? "
            "AND section_number=? AND edition_year=? AND appendix_flag=0",
            (t, s, diff_edition)).fetchone()
        if other:
            body_html = diff_html(sec["body_text"], other[0] or "")
            compare = (f'<p class="mut">Comparing edition {edition} '
                       f'(base) with {diff_edition}: '
                       f'<span class="ins">green = added in {diff_edition}</span>, '
                       f'<span class="del">red = present only in {edition}</span>. '
                       f'<a href="/usc/{t}/{s}?edition={diff_edition}&diff={edition}">'
                       f'Swap direction</a></p>')
    cmp_opts = " ".join(
        f'<a href="/usc/{t}/{s}?edition={edition}&diff={e}">{e}</a>'
        for e in USC_EDITIONS if e != edition)
    body = f"""
<h1>{esc(sec["citation"])}</h1>
<p class="mut">{esc(sec.get("heading") or "")} ·
 Title {t}
{(" · Subtitle " + esc(sec["subtitle"])) if sec.get("subtitle") else ""}
{(" · Part " + esc(sec["part"])) if sec.get("part") else ""}
{(" · Chapter " + esc(sec["chapter"])) if sec.get("chapter") else ""}
{(" · Subchapter " + esc(sec["subchapter"])) if sec.get("subchapter") else ""}</p>
<h2>Editions</h2>
<div class="editions">{eds}</div>
<p class="mut">† text differs from the 2018 Main Edition. Current default:
 {CURRENT_EDITION}.</p>
<h2>Compare editions</h2>
<p class="mut">Compare current ({edition}) with: {cmp_opts}</p>
{compare}
<div class="card"><div class="lawtext">{body_html}</div></div>
<p class="mut">Source: U.S. Government Publishing Office ·
 <a href="{esc(sec.get("source_url") or "")}">GPO package</a></p>"""
    return page(sec["citation"], body)


def diff_html(old: str, new: str) -> str:
    """Word-level diff → HTML (del/ins highlighting)."""
    import difflib
    a, b = old.split(), new.split()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.append(esc(" ".join(a[i1:i2])))
        elif tag == "delete":
            out.append(f'<span class="del">{esc(" ".join(a[i1:i2]))}</span>')
        elif tag == "insert":
            out.append(f'<span class="ins">{esc(" ".join(b[j1:j2]))}</span>')
        elif tag == "replace":
            out.append(f'<span class="del">{esc(" ".join(a[i1:i2]))}</span>'
                       f'<span class="ins">{esc(" ".join(b[j1:j2]))}</span>')
        out.append(" ")
    return "".join(out)


def render_cfr(sec: dict | None, title: str, secno: str) -> str:
    if not sec:
        return page("Not found",
                    f'<h1>Section not found</h1><p>No CFR section '
                    f'{esc(title)} § {esc(secno)} in the database.</p>')
    authority = (sec.get("authority") or "").strip()
    citations = (sec.get("citations") or "").strip()
    if citations.startswith("[") and citations.endswith("]"):
        try:
            import json as _json
            citations = "; ".join(
                str(c) for c in _json.loads(citations) if str(c).strip())
        except (ValueError, TypeError):
            pass
    auth_html = (f'<h2>Authority</h2><div class="card">{esc(authority)}</div>'
                 if authority else "")
    cit_html = (f'<h2>Amendments</h2><div class="card">{esc(citations)}</div>'
                if citations else "")
    body = f"""
<h1>{title} C.F.R. § {esc(sec["identifier"])}</h1>
<p class="mut">{esc(sec.get("name") or "")} · snapshot {esc(sec["snapshot_date"])}</p>
<div class="card"><div class="lawtext">{esc(sec["body_text"])}</div></div>
{auth_html}{cit_html}"""
    return page(f"{title} CFR § {sec['identifier']}", body)


def render_cases(q: str, mode: str, court: str, dfrom: str, dto: str,
                 res: dict, page_no: int, per: int, sort: str = "relevance") -> str:
    court_opts = '<option value="">All courts</option>' + "".join(
        f'<option value="{cid}"{" selected" if court == cid else ""}>{name}</option>'
        for cid, name in COURTS.items())
    trunc = ('<p class="mut">Results truncated at '
             f'{FTS_TOP:,} matching opinions — refine your query.</p>'
             if res.get("truncated") else "")
    body = f"""
<h1>Case Law Search</h1>
<form action="/cases" method="get">
 <input class="bigbox" type="text" name="q" value="{esc(q)}"
        placeholder="full-text keywords or party names">
 <select name="mode">
  <option value="fts"{' selected' if mode != 'name' else ''}>full text</option>
  <option value="name"{' selected' if mode == 'name' else ''}>case name</option>
 </select>
 <select name="court">{court_opts}</select>
 <select name="sort">
  <option value="relevance"{' selected' if sort != 'date' else ''}>relevance</option>
  <option value="date"{' selected' if sort == 'date' else ''}>newest first</option>
 </select>
 <input type="text" name="from" value="{esc(dfrom)}" placeholder="from 1990-01-01"
        style="width:120px">
 <input type="text" name="to" value="{esc(dto)}" placeholder="to 2020-12-31"
        style="width:120px">
 <button type="submit">Search</button>
</form>
<p class="mut">{res['total']:,} case cluster{"s" if res["total"] != 1 else ""} found.</p>
{trunc}"""
    if res["rows"]:
        body += '<table><tr><th>Case</th><th>Citation</th><th>Court</th><th>Date</th></tr>'
        for r in res["rows"]:
            body += (f'<tr><td><a href="/case/{r["id"]}">'
                     f'{esc(r["case_name"])}</a>'
                     + (f'<div class="snippet">{esc(r.get("snippet",""))}</div>'
                        if r.get("snippet") else "") +
                     f'</td><td>{esc(r.get("cites") or "")}</td>'
                     f'<td>{esc(COURTS.get(r.get("court_id"), r.get("court_id") or ""))}</td>'
                     f'<td>{esc(r.get("date_filed") or "")}</td></tr>')
        body += "</table>"
        body += pager_html(page_no, per, res["total"],
                           f"/cases?q={quote(q)}&mode={mode}&court={quote(court)}"
                           f"&sort={sort}&from={quote(dfrom)}&to={quote(dto)}")
    return page("Case Law Search", body)


def pager_html(page_no: int, per: int, total: int, base_url: str) -> str:
    import math
    pages = max(1, math.ceil(total / per))
    if pages <= 1:
        return ""
    page_no = max(1, min(page_no, pages))  # clamp out-of-range page so win is never empty
    base_url = base_url.rstrip("&?")       # strip trailing connector to unify sep
    sep = "&" if "?" in base_url else "?"
    out = ['<div class="pager">']
    win = [p for p in range(max(1, page_no - 4), min(pages, page_no + 4) + 1)]
    if win[0] > 1:
        out.append(f'<a href="{base_url}{sep}page=1">1</a>'
                   + ('…' if win[0] > 2 else ''))
    for p in win:
        out.append(f'<span class="cur">{p}</span>' if p == page_no
                   else f'<a href="{base_url}{sep}page={p}">{p}</a>')
    if win[-1] < pages:
        out.append(('…' if win[-1] < pages - 1 else '')
                   + f'<a href="{base_url}{sep}page={pages}">{pages}</a>')
    out.append("</div>")
    return "".join(out)


def render_case(cid: str, d: dict | None, citing: dict, cited: list,
                page_no: int, per: int, cq: str = "", ccourt: str = "") -> str:
    if not d:
        return page("Not found",
                    f'<h1>Case not found</h1><p>No cluster {esc(cid)}.</p>')
    body = [f"""
<h1>{esc(d["case_name"])}</h1>
<p class="mut">{esc(d["cites_str"] or "no reporter citation")} ·
 {esc(d["court_name"])} · {esc(d.get("date_filed") or "date unknown")}
{(" · Docket " + esc(d["docket_number"])) if d.get("docket_number") else ""}
{(" · " + esc(d["judges"])) if d.get("judges") else ""}</p>"""]
    for o in d["opinions"]:
        body.append(f'<div class="opsel">{esc(o["label"])}'
                    + (" (per curiam)" if o.get("per_curiam") else "")
                    + f' <span class="mut">· {len(o["text"]):,} chars</span></div>')
        if o["text"]:
            body.append(f'<div class="card"><div class="lawtext">'
                        f'{esc(o["text"])}</div></div>')
    if cited:
        body.append(f'<h2>Authorities cited ({len(cited)} shown)</h2>'
                    '<table><tr><th>Case</th><th>Citation</th>'
                    '<th>Depth</th><th>Date</th></tr>')
        for r in cited:
            body.append(f'<tr><td><a href="/case/{r["cid"]}">'
                        f'{esc(r["case_name"])}</a></td>'
                        f'<td>{esc(r.get("cites") or "")}</td>'
                        f'<td>{r["depth"]}</td>'
                        f'<td>{esc(r.get("date_filed") or "")}</td></tr>')
        body.append("</table>")
    ct = citing["total"]
    body.append(f'<h2>Citing references — {ct:,}</h2>')
    court_opts = ('<option value="">any court</option>' + "".join(
        f'<option value="{k}"{" selected" if ccourt == k else ""}>{v}</option>'
        for k, v in COURTS.items()))
    body.append(f"""
<div class="filterbar"><form method="get" action="/case/{esc(cid)}">
 <input type="text" name="q" value="{esc(cq)}"
        placeholder="filter citing cases (name or parenthetical)" style="width:280px">
 <select name="court">{court_opts}</select>
 <button type="submit">Filter</button>
</form></div>""")
    if citing["rows"]:
        body.append('<table><tr><th>Citing case</th><th>Court</th>'
                    '<th>Date</th></tr>')
        for r in citing["rows"]:
            body.append(f'<tr><td><a href="/case/{r["cid"]}">'
                        f'{esc(r["case_name"])}</a>'
                        + (f'<div class="paren">({esc(r["paren"])})</div>'
                           if r.get("paren") else "") +
                        f'</td><td>{esc(COURTS.get(r.get("court_id"), r.get("court_id") or ""))}</td>'
                        f'<td>{esc(r.get("date_filed") or "")}</td></tr>')
        body.append("</table>")
        body += pager_html(page_no, per, ct,
                           f"/case/{cid}?q={quote(cq)}&court={quote(ccourt)}&")
    else:
        body.append('<p class="mut">No citing references in this collection.</p>')
    return page(d["case_name"], "".join(body))


def render_stats() -> str:
    c = db()
    per_year = c.execute("SELECT edition_year, COUNT(*) FROM usc_sections "
                         "GROUP BY edition_year ORDER BY edition_year").fetchall()
    rows = "".join(f'<tr><td>{y}</td><td>{n:,}</td></tr>' for y, n in per_year)
    body = f"""
<h1>About the Data</h1>
<div class="card">
<table>
<tr><th>Collection</th><th>Contents</th></tr>
<tr><td>United States Code</td><td>{sum(n for _, n in per_year):,} sections across
 editions {USC_EDITIONS[0]}–{CURRENT_EDITION} (GPO annual compilations).</td></tr>
<tr><td>Code of Federal Regulations</td><td>{c.execute(
    "SELECT COUNT(*) FROM cfr_sections WHERE body_text != ''").fetchone()[0]:,}
 current sections (eCFR snapshot).</td></tr>
<tr><td>Federal case law</td><td>Supreme Court of the United States and the
 thirteen federal courts of appeals, 1759–present.</td></tr>
<tr><td>Citation index</td><td>Reporter citations, U.S.C. and C.F.R.
 citations resolved to full text.</td></tr>
</table></div>
<h2>U.S. Code by edition</h2>
<div class="card"><table><tr><th>Edition</th><th>Sections</th></tr>{rows}
</table></div>"""
    return page("About the Data", body)


# --------------------------------------------------------------------------
# HTTP service
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    friction: Friction = Friction()
    server_version = "UniUSLaw/1.0"
    _hits = 0
    _lock = threading.Lock()

    def log_message(self, fmt, *args):
        pass

    def _log_access(self, key: str, detail: str = ""):
        if self.friction.access_log:
            with open(self.friction.access_log, "a") as f:
                f.write(json.dumps({"t": time.time(), "key": key,
                                    "detail": detail}) + "\n")

    def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False, indent=1),
                   "application/json; charset=utf-8")

    def _wants_json(self, qs: dict) -> bool:
        if self.friction.no_json:
            return False
        if qs.get("format", [""])[0].lower() == "json":
            return True
        return "application/json" in (self.headers.get("Accept") or "")

    def _rate_limited(self) -> bool:
        if not self.friction.rate_limit:
            return False
        with Handler._lock:
            Handler._hits += 1
            return Handler._hits % self.friction.rate_limit == 0

    def do_GET(self):
        try:
            self._route()
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            self._send(500, page("Error", f"<h1>Server error</h1><p>{esc(e)}</p>"))

    def _route(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if self._rate_limited():
            self._send(200, page("Rate limited",
                                 "<h1>Request throttled</h1>"
                                 "<p>Please retry in a moment.</p>"))
            return
        path = unquote(u.path).rstrip("/") or "/"
        if path == "/":
            self._send(200, home(qs.get("q", [""])[0]))
            return
        if path == "/stats":
            if self._wants_json(qs):
                c = db()
                self._json({"usc_per_edition": dict(c.execute(
                    "SELECT edition_year, COUNT(*) FROM usc_sections "
                    "GROUP BY edition_year").fetchall()),
                    "cfr": c.execute("SELECT COUNT(*) FROM cfr_sections "
                                     "WHERE body_text != ''").fetchone()[0]})
            else:
                self._send(200, render_stats())
            return
        if path == "/search":
            self._h_search(qs)
            return
        if path == "/cases":
            self._h_cases(qs)
            return
        if path.startswith("/usc/"):
            self._h_usc(path, qs)
            return
        if path.startswith("/cfr/"):
            self._h_cfr(path, qs)
            return
        if path.startswith("/case/"):
            self._h_case(path, qs)
            return
        if path.startswith("/cite/"):
            self._h_cite(path)
            return
        if path.startswith("/statute/"):
            # ref_id shapes: usc/2024/title17/ch1/sec107 / cfr/21/1.1/2026-08-20
            ref = path[len("/statute/"):]
            parts = ref.split("/")
            if parts[0] == "usc" and len(parts) >= 5:
                self._send(302, "")
                self.send_header("Location",
                                 f"/usc/{parts[2][5:]}/{parts[4][3:]}")
            elif parts[0] == "cfr" and len(parts) >= 4:
                self._send(302, "")
                self.send_header("Location", f"/cfr/{parts[1]}/{parts[2]}")
            else:
                self._send(404, page("Not found", "<h1>Bad statute ref</h1>"))
            return
        self._send(404, page("Not found", "<h1>404</h1>"))

    # -- handlers ----------------------------------------------------------

    def _h_search(self, qs):
        q = (qs.get("q", [""])[0] or "").strip()
        kind = qs.get("kind", ["all"])[0] or "all"
        page_no = max(1, int_or(qs.get("page", ["1"])[0], 1))
        per = self.friction.pagination
        resolved = None
        results = {"total": 0, "rows": []}
        case_res = {"total": 0, "rows": []}
        if q:
            cls = classify_citation(q)
            if cls and cls["kind"] == "case":
                resolved = resolve_citation(cls["citation"])
            elif cls:
                resolved = {"citation": q,
                            "ref_type": "statute" if cls["kind"] == "usc" else "regulation",
                            "ref_id": (f"usc/{CURRENT_EDITION}/title{cls['title']}"
                                       f"/chx/sec{cls['sec']}" if cls["kind"] == "usc"
                                       else f"cfr/{cls['title']}/{cls['sec']}/x")}
            results = search_statutes(q, kind, page_no, per)
            case_res = search_cases(q, "", "", "", "fts", 1, per)
            if self.friction.snippet_only:
                for r in results["rows"]:
                    r.pop("snip", None)
                for r in case_res["rows"]:
                    r.pop("snippet", None)
            self._log_access("search", q)
        if self._wants_json(qs):
            self._json({"q": q, "kind": kind, "resolved": resolved,
                        "statutes": results,
                        "cases": {"total": case_res["total"],
                                  "truncated": case_res.get("truncated", False),
                                  "clusters": [
                                      {k: r[k] for k in
                                       ("id", "case_name", "date_filed",
                                        "court_id", "cites")}
                                      for r in case_res["rows"]]}})
        else:
            self._send(200, render_search(q, kind, results, case_res,
                                          resolved, page_no, per))

    def _h_cases(self, qs):
        q = (qs.get("q", [""])[0] or "").strip()
        mode = qs.get("mode", ["fts"])[0] or "fts"
        court = qs.get("court", [""])[0] or ""
        dfrom = qs.get("from", [""])[0] or ""
        dto = qs.get("to", [""])[0] or ""
        sort = qs.get("sort", ["relevance"])[0] or "relevance"
        page_no = max(1, int_or(qs.get("page", ["1"])[0], 1))
        per = self.friction.pagination
        res = (search_cases(q, court, dfrom, dto, mode, page_no, per, sort)
               if (q or court or dfrom or dto)
               else {"total": 0, "truncated": False, "rows": []})
        if self.friction.snippet_only:
            for r in res["rows"]:
                r.pop("snippet", None)
        if q:
            self._log_access("case_search", f"{q}|{mode}|{court}")
        if self._wants_json(qs):
            self._json({"q": q, "mode": mode, "court": court, "sort": sort,
                        "from": dfrom, "to": dto, **res})
        else:
            self._send(200, render_cases(q, mode, court, dfrom, dto, res,
                                         page_no, per, sort))

    def _h_usc(self, path, qs):
        parts = [p for p in path.split("/") if p]  # ['usc', ...]
        if len(parts) >= 2 and not parts[1].isdigit():
            self._send(404, page("Not found", "<h1>Bad USC title</h1>"))
            return
        try:
            edition = int(qs.get("edition", [str(CURRENT_EDITION)])[0])
            if edition not in USC_EDITIONS:
                edition = CURRENT_EDITION
        except ValueError:
            edition = CURRENT_EDITION
        # /usc/<title>            Title catalog (chapters)
        if len(parts) == 2:
            b = usc_title_browse(parts[1], edition)
            self._log_access("usc_browse", f"t{parts[1]}@{edition}")
            if self._wants_json(qs):
                self._json(b or {"error": "not found"})
            else:
                self._send(200, render_usc_browse(b, parts[1], edition))
            return
        # /usc/<title>/ch/<ch>    section list within a chapter
        if len(parts) == 4 and parts[2] == "ch":
            b = usc_chapter_browse(parts[1], parts[3], edition)
            self._log_access("usc_chapter", f"t{parts[1]}ch{parts[3]}@{edition}")
            if self._wants_json(qs):
                self._json(b or {"error": "not found"})
            else:
                self._send(200, render_usc_chapter(b, parts[1], parts[3], edition))
            return
        # /usc/<title>/<sec>      section page (may carry diff=<edition>)
        if len(parts) >= 3:
            try:
                diff_ed = int(qs.get("diff", [""])[0] or 0)
                if diff_ed not in USC_EDITIONS or diff_ed == edition:
                    diff_ed = None
            except ValueError:
                diff_ed = None
            sec = usc_section(parts[1], parts[2], edition)
            self._log_access("usc", f"{parts[1]}§{parts[2]}@{edition}")
            if self._wants_json(qs):
                self._json(sec or {"error": "not found"})
            else:
                err = None if sec else (
                    f"No § {esc(parts[2])} in Title {esc(parts[1])} "
                    f"(edition {edition}).")
                self._send(200, render_usc(sec, edition, err, diff_ed))
            return
        self._send(404, page("Not found", "<h1>Bad USC path</h1>"))

    def _h_cfr(self, path, qs):
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and not parts[1].isdigit():
            self._send(404, page("Not found", "<h1>Bad CFR title</h1>"))
            return
        if len(parts) == 2:
            b = cfr_title_browse(parts[1])
            self._log_access("cfr_browse", f"t{parts[1]}")
            if self._wants_json(qs):
                self._json(b or {"error": "not found"})
            else:
                self._send(200, render_cfr_browse(b, parts[1]))
            return
        # /cfr/<title>/p/<part>   part detail (section list under that part)
        if len(parts) == 4 and parts[2] == "p":
            b = cfr_part_sections(parts[1], parts[3])
            self._log_access("cfr_part", f"t{parts[1]}p{parts[3]}")
            if self._wants_json(qs):
                self._json(b or {"error": "not found"})
            else:
                self._send(200, render_cfr_part(b, parts[1], parts[3]))
            return
        if len(parts) >= 3:
            sec = cfr_section(parts[1], parts[2])
            self._log_access("cfr", f"{parts[1]}§{parts[2]}")
            if self._wants_json(qs):
                self._json(sec or {"error": "not found"})
            else:
                self._send(200, render_cfr(sec, parts[1], parts[2]))
            return
        self._send(404, page("Not found", "<h1>Bad CFR path</h1>"))

    def _h_case(self, path, qs):
        cid = path.split("/")[2]
        page_no = max(1, int_or(qs.get("page", ["1"])[0], 1))
        cq = (qs.get("q", [""])[0] or "").strip()
        ccourt = qs.get("court", [""])[0] or ""
        per = self.friction.pagination
        d = case_detail(cid)
        citing = (citing_references(cid, per, (page_no - 1) * per, cq, ccourt)
                  if d else {"total": 0, "rows": []})
        cited = cited_authorities(cid, 25) if d else []
        self._log_access("case", cid + (f"|q={cq}" if cq else ""))
        if self._wants_json(qs):
            self._json({"case": d, "citing": citing, "cited": cited}
                       if d else {"error": "not found"})
        else:
            self._send(200, render_case(cid, d, citing, cited, page_no, per,
                                        cq, ccourt))

    def _h_cite(self, path):
        cit = unquote(path[len("/cite/"):])
        r = resolve_citation(cit)
        if not r:
            cls = classify_citation(cit)
            if cls and cls["kind"] != "case":
                r = {"ref_type": "statute" if cls["kind"] == "usc" else "regulation",
                     "citation": cit,
                     "ref_id": (f"usc/{CURRENT_EDITION}/title{cls['title']}"
                                f"/chx/sec{cls['sec']}"
                                if cls["kind"] == "usc"
                                else f"cfr/{cls['title']}/{cls['sec']}/x")}
        if not r:
            self._send(404, page("Not resolved",
                                 f"<h1>Citation not found</h1>"
                                 f"<p>{esc(cit)}</p>"))
            return
        self._log_access("cite", cit)
        if r["ref_type"] == "case":
            loc = f"/case/{r['ref_id']}"
        elif r["ref_type"] == "statute":
            _, yr, t, ch, s = r["ref_id"].split("/")
            loc = f"/usc/{t[5:]}/{s[3:]}"
        else:
            p = r["ref_id"].split("/")
            loc = f"/cfr/{p[1]}/{p[2]}"
        self.send_response(302)
        self.send_header("Location", loc)
        self.send_header("Content-Length", "0")
        self.end_headers()


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def selftest() -> int:
    fails = []
    assert db().execute("SELECT 1").fetchone()[0] == 1
    r = resolve_citation("347 U.S. 483")
    if not (r and r["display_name"] == "Brown v. Board of Education"):
        fails.append(f"Brown resolve: {r}")
    r2 = resolve_citation("17 U.S.C. § 106")
    if not (r2 and r2["ref_type"] == "statute"):
        fails.append(f"§106 resolve: {r2}")
    s = usc_section("17", "107", 2024)
    if not (s and "Fair use" in (s["heading"] or "")
            and len(s["versions"]) == 7):
        fails.append(f"§107 detail: {s and s['heading']}, "
                     f"versions={s and len(s['versions'])}")
    s18 = usc_section("17", "107", 2018)
    if not (s18 and "purposes such as criticism" in s18["body_text"]):
        fails.append("§107 2018 body missing")
    st = search_statutes("fair use", "usc", 1, 10)
    if st["total"] < 5:
        fails.append(f"statute fts: {st['total']}")
    cs = search_cases("separate sovereigns double jeopardy", "", "", "",
                      "fts", 1, 10)
    if cs["total"] < 3:
        fails.append(f"case fts: {cs['total']}")
    d = case_detail("105221")
    if not (d and d["case_name"] == "Brown v. Board of Education"
            and d["opinions"]):
        fails.append(f"Brown detail: {d and d['case_name']}")
    cr = citing_references("105221", 10, 0)
    # The release bundle is a case subset (citation edges kept only inside it), so Brown's
    # citing count here is far below the full corpus; threshold adapts to corpus size: full ≥1000, subset (<100k clusters) ≥50.
    n_clusters = db().execute("SELECT count(*) FROM cl_clusters").fetchone()[0]
    cr_min = 1000 if n_clusters >= 100_000 else 50
    if cr["total"] < cr_min:
        fails.append(f"Brown citing: {cr['total']} (min={cr_min})")
    ca = cited_authorities("105221", 5)
    if not ca:
        fails.append("Brown cited authorities empty")
    cfr = cfr_section("21", "1.1")
    if not (cfr and cfr["body_text"]):
        fails.append("21 CFR 1.1 empty")
    # v2: title browse / chapter / chapter name / diff / citing filters / FTS dedup
    tb = usc_title_browse("17", 2024)
    if not (tb and tb["total"] > 140 and len(tb["chapters"]) >= 13):
        fails.append(f"title17 browse: total={tb and tb['total']}, "
                     f"chapters={tb and len(tb['chapters'])}")
    chb = usc_chapter_browse("17", "1", 2024)
    if not (chb and any(s["section_number"] == "107" for s in chb["sections"])):
        fails.append("title17 ch1 browse missing §107")
    cb = cfr_title_browse("21")
    if not (cb and cb["parts"]):
        fails.append("cfr title21 browse empty")
    dh = diff_html("one two three", "one TOO three")
    if not ('class="ins"' in dh and 'class="del"' in dh):
        fails.append(f"diff_html: {dh!r}")
    cr_q = citing_references("105221", 10, 0, q="Board of Education")
    if not (0 < cr_q["total"] < cr["total"]):
        fails.append(f"citing filter q: {cr_q['total']} vs {cr['total']}")
    cr_c = citing_references("105221", 10, 0, court="scotus")
    if not (0 < cr_c["total"] < cr["total"]):
        fails.append(f"citing filter court: {cr_c['total']}")
    hot = search_cases("the", "", "", "", "fts", 1, 20)
    if not (hot["total"] > 0 and "truncated" in hot):
        fails.append("hot-word fts missing truncated flag")
    cold = search_cases("cyberespionage", "", "", "", "fts", 1, 20)
    if cold["total"] < 1 or cold.get("truncated"):
        fails.append(f"cold-word fts: {cold['total']}")
    # v2.1: citation resolve → jump link (ref_id split was once a bug, generating /usc/17/x/sec107)
    html_resolved = render_search(
        "17 U.S.C. § 106", "all", {"total": 0, "rows": []},
        {"total": 0, "rows": []},
        {"ref_type": "statute", "citation": "17 U.S.C. § 106",
         "ref_id": "usc/2024/title17/chx/sec106",
         "display_name": "Exclusive rights"}, 1, 20)
    if '/usc/17/106' not in html_resolved:
        fails.append("resolved statute link broken in render_search")
    html_reg = render_search(
        "21 CFR § 1.1", "all", {"total": 0, "rows": []},
        {"total": 0, "rows": []},
        {"ref_type": "regulation", "citation": "21 CFR § 1.1",
         "ref_id": "cfr/21/1.1/x"}, 1, 20)
    if '/cfr/21/1.1' not in html_reg:
        fails.append("resolved regulation link broken in render_search")
    # v2.2: kind=usc-2018 filter (source uses slash usc/2018; hyphenated kind used to mismatch)
    k18 = search_statutes("copyright", "usc-2018", 1, 10)
    if not (k18["total"] > 0 and
            all(r["source"] == "usc/2018" for r in k18["rows"])):
        fails.append(f"kind=usc-2018 filter: total={k18['total']}, "
                     f"sources={set(r['source'] for r in k18['rows'])}")
    # v2.3: pager out-of-range page + trailing connector (once IndexError / emitted &&page)
    pg = pager_html(10000, 20, 4504, '/cases?q=x&')
    if not (pg and '<span class="cur">226</span>' in pg and '&&' not in pg):
        fails.append(f"pager clamp: {pg!r}")
    pg2 = pager_html(1, 20, 4504, '/case/105221?q=x&court=scotus&')
    if '&&' in pg2 or '?&' in pg2:
        fails.append(f"pager sep: {pg2!r}")
    # v2.4: snippet_only depends on field names (statute=snip, case=snippet; pop relies on this convention)
    s_rows = search_statutes("fair use", "usc", 1, 5)["rows"]
    if s_rows and "snip" not in s_rows[0]:
        fails.append("statute rows missing 'snip' (snippet_only pop would break)")
    c_rows = search_cases("fair use", "", "", "", "fts", 1, 5)["rows"]
    if c_rows and "snippet" not in c_rows[0]:
        fails.append("case rows missing 'snippet' (snippet_only pop would break)")
    # v2.5: citation case-insensitivity (USC/CFR branches used to accept uppercase only)
    if classify_citation('17 u.s.c. § 107') is None:
        fails.append("lowercase USC citation not classified")
    if classify_citation('21 c.f.r. § 1.1') is None:
        fails.append("lowercase CFR citation not classified")
    if not resolve_citation('347 u.s. 483'):
        fails.append("lowercase case citation not resolved")
    # v2.6: USC/CFR shorthand without § (42 U.S.C. 1983 was once misclassified as a case citation)
    if classify_citation('42 U.S.C. 1983') != {'kind': 'usc', 'title': '42', 'sec': '1983'}:
        fails.append("dotted-no-section USC shorthand not recognized")
    if classify_citation('42 USC 1983') is None:
        fails.append("no-dot-no-section USC shorthand not recognized")
    if classify_citation('21 CFR 1.1') is None:
        fails.append("no-dot CFR shorthand not recognized")
    if (classify_citation('347 U.S. 483') or {}).get('kind') != 'case':
        fails.append("case citation misclassified as USC")
    # v2.7: opinion type label mapping (040dissent/025plurality once mislabeled as default Opinion)
    if op_label('040dissent') != 'Dissenting Opinion':
        fails.append(f"dissent label: {op_label('040dissent')}")
    if op_label('025plurality') != 'Plurality Opinion':
        fails.append(f"plurality label: {op_label('025plurality')}")
    if op_label('035concurrenceinpart') != 'Concurring in Part':
        fails.append(f"concur-part label: {op_label('035concurrenceinpart')}")
    # v2.8: render_cfr shows authority (statutory basis; 97.6% coverage after the 2026-08-23 reload)
    cfr_html = render_cfr(cfr_section("1", "1.1"), "1", "1.1")
    if "Authority" not in cfr_html or "44 U.S.C." not in cfr_html:
        fails.append("render_cfr missing authority display")
    # v2.9: robustness to bad input (int_or prevents page=abc / non-numeric title 500s)
    if int_or('abc', 5) != 5 or int_or('10', 5) != 10 or int_or(None, 7) != 7:
        fails.append("int_or parse failure")
    # v2.10: CFR catalog part links (once landed on "Section not found")
    pb = cfr_part_sections("21", "1")
    if not (pb and pb["sections"] and
            any(s["identifier"] == "1.1" for s in pb["sections"])):
        fails.append("cfr_part_sections 21/1 empty or missing §1.1")
    # v2.11: compound section hyphen/en dash normalization (§285a-1 was unfindable, stored with en dash)
    if usc_section("42", "285a-1", 2024) is None:
        fails.append("compound section hyphen→en dash normalization failed")
    # v2.12: case citation variant normalization (dotless/double-space forms once missed)
    if resolve_citation("347 US 483") is None:
        fails.append("dotless case citation fallback failed")
    if resolve_citation("347  U.S.  483") is None:
        fails.append("double-space case citation normalization failed")
    # v2.13: date filter NUMERIC affinity mixed-comparison bug (bare '2010' was always False, returning 0)
    if search_cases("Brown", "scotus", "", "2010", "name", 1, 5)["total"] == 0:
        fails.append("date filter to='2010' broken (expected >0)")
    if fails:
        print("SELFTEST FAILED:")
        for f in fails:
            print(" -", f)
        return 1
    print("selftest: all checks passed")
    print(f"  db: {DB_PATH}")
    return 0


def main() -> int:
    global DB_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--db", default=None)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    DB_PATH = args.db or find_db()

    if args.selftest:
        return selftest()

    Handler.friction = Friction.from_env()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"{SITE_NAME} serving on http://127.0.0.1:{args.port}")
    print(f"  db: {DB_PATH}")
    print(f"  friction: {Handler.friction.describe()}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
