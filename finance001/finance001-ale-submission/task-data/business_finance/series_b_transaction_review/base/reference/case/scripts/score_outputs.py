"""Deterministic, reference-backed scorer for the nine finance001 deliverables."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PATH = ROOT / "case" / "reference" / "EXPECTED.json"

# The evaluation environment is a contract, not a discovery. A missing third-party
# reader used to be swallowed into an empty PDF text layer, which scored a compliant
# memo as non-compliant and zeroed a whole capability dimension with no warning, so the
# same submission scored differently on two machines. Requirements live beside this
# file in `requirements.txt`; the preflight below refuses to score without them rather
# than degrading silently.
MIN_PYTHON = (3, 10)
REQUIRED_MODULES = ("pypdf",)


class EnvironmentContractError(RuntimeError):
    """The scoring host does not satisfy the declared evaluator environment."""


def check_environment() -> list[str]:
    problems: list[str] = []
    if sys.version_info < MIN_PYTHON:
        problems.append(
            "Python {}.{} or newer is required; this interpreter is {}.{}.{}".format(
                *MIN_PYTHON, *sys.version_info[:3]
            )
        )
    for module in REQUIRED_MODULES:
        try:
            __import__(module)
        except ImportError:
            problems.append(f"required module {module!r} is not installed")
    return problems


def require_environment() -> None:
    problems = check_environment()
    if problems:
        raise EnvironmentContractError(
            "evaluator environment contract not met:\n  - "
            + "\n  - ".join(problems)
            + "\ninstall the declared dependencies: "
            + "python -m pip install -r case/scripts/requirements.txt"
        )


REQUIRED_ARTIFACTS = (
    "term_sheet_extract.json",
    "capitalization.xlsx",
    "economics.json",
    "deviation_register.csv",
    "negotiation_memo.pdf",
    "redline_positions.csv",
    "ic_recommendation.json",
    "reconciliation.csv",
    "instrument_register.csv",
)

TOL_SHARES = Decimal("0")
TOL_USD_CENT = Decimal("0.01")
TOL_PPS_6DP = Decimal("0.000001")
TOL_PCT_4DP = Decimal("0.0001")
TOL_IRR_4DP = Decimal("0.0001")
TOL_RATIO = Decimal("0.000000001")

DIMENSIONS = {
    "D1": "Document arbitration and provenance",
    "D2": "Convertible conversion mechanics",
    "D3": "Option pool shuffle and pricing",
    "D4": "Capitalization construction and ownership",
    "D5": "Waterfall and preference mechanics",
    # D6 "Anti-dilution and scenario overlays" was retired on 2026-08-11. Its eighteen
    # positions scored adjusted conversion prices and pay-to-play participation for two
    # down-round scenarios whose date, price, share count and participating-holder set
    # existed only in tools/facts.py. The agent-facing schema said the scenarios come
    # "from the governing corpus"; they were in none of the 336 delivered files -- the
    # price 2.750000 appears nowhere in the corpus at all -- so no submission could reach
    # them. Blind trial 06 surfaced this by reporting the absence and inventing two
    # scenarios of its own. See docs/PROBE.md.
    "D7": "Returns",
    "D8": "Market benchmarking and deviation classification",
    "D9": "Policy gates and conditions precedent",
    "D10": "Negotiation prioritisation and pricing",
    "D11": "Cross-file reconciliation",
    "D12": "Tooling and environment discipline",
    "D13": "Schema and format compliance",
    "D14": "Production discipline",
}


@dataclass(frozen=True)
class Check:
    no: int
    dimension: str
    starred: bool
    description: str
    deliverable: str
    field: str
    assertion: str
    obligation: str
    evaluator: Callable[[dict[str, Any], dict[str, Any]], tuple[bool, str, str]]


CHECKS: list[Check] = []
RESULTS: list[dict[str, Any]] = []
ALL_STARS: frozenset[int] = frozenset()


def number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    text = re.sub(r"[$%xX\s]", "", text)
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def eq(got: Any, want: Any, *, tol: Decimal, kind: str) -> bool:
    left, right = number(got), number(want)
    return left is not None and right is not None and abs(left - right) <= tol


def _unwrap(node: Any, part: str | None = None) -> Any:
    """Step through a provenance envelope.

    `economics.json`'s schema requires every reported item to carry its value, unit,
    source files, source sections and workbook reference - which a bare scalar cannot do,
    so the compliant form is `{"value": ..., "unit": ..., ...}`. The grader used to read
    only bare scalars, so a submission that followed the schema scored zero on every
    value it got right: blind trial 05 reported pps, the pre-money fully diluted count,
    the post-financing total and both ownership percentages exactly correctly inside such
    envelopes and was awarded nothing for any of them, with G1 failing on ownership
    fields it had in fact reported. Demanding a bare scalar contradicted the published
    schema, so both forms are now read the same way.

    `part` is the key about to be looked up: an envelope is only stepped through when the
    key is not present on the envelope itself, so an item legitimately named `value` or a
    section that happens to carry a `value` key is not mistaken for one.
    """
    while (
        isinstance(node, dict)
        and "value" in node
        and (part is None or part not in node)
    ):
        node = node["value"]
    return node


def _lookup(root: Any, dotted: str) -> Any:
    current = root
    for part in dotted.split("."):
        current = _unwrap(current, part)
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(dotted)
    return _unwrap(current)


def _value_check(path: str, expected_path: str, tol: Decimal, kind: str) -> Callable:
    def evaluate(output: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, str, str]:
        try:
            got = _lookup(output, path)
            want = _lookup(expected, expected_path)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return False, "missing", str(exc)
        ok = eq(got, want, tol=tol, kind=kind)
        return ok, "pass" if ok else "wrong", f"got={got!r} want={want!r}"
    return evaluate


def _exact_check(path: str, expected_path: str) -> Callable:
    def evaluate(output: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, str, str]:
        try:
            got = _lookup(output, path)
            want = _lookup(expected, expected_path)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return False, "missing", str(exc)
        ok = got == want
        return ok, "pass" if ok else "wrong", f"got={got!r} want={want!r}"
    return evaluate


def _register(
    no: int,
    dimension: str,
    starred: bool,
    description: str,
    deliverable: str,
    field: str,
    assertion: str,
    obligation: str,
    evaluator: Callable,
) -> None:
    CHECKS.append(Check(no, dimension, starred, description, deliverable, field, assertion, obligation, evaluator))


def _build_registry() -> None:
    if CHECKS:
        return
    no = 1
    scalar_specs = (
        ("outer_iterations", "D3", TOL_SHARES, "integer", False),
        ("base_shares", "D4", TOL_SHARES, "shares", False),
        ("net_warrant_shares", "D4", TOL_SHARES, "shares", False),
        ("warrant_shares_full_exercise", "D4", TOL_SHARES, "shares", False),
        ("convertible_shares_total", "D2", TOL_SHARES, "shares", False),
        ("plan_reserve_unissued", "D3", TOL_SHARES, "shares", False),
        ("pool_increase", "D3", TOL_SHARES, "shares", True),
        ("pre_money_fd_Q", "D3", TOL_SHARES, "shares", True),
        ("pps", "D3", TOL_PPS_6DP, "pps", True),
        ("round_shares_t1", "D4", TOL_SHARES, "shares", False),
        ("round_shares_full", "D4", TOL_SHARES, "shares", False),
        ("ardent_shares_t1", "D4", TOL_SHARES, "shares", False),
        ("fd_post_first_close", "D4", TOL_SHARES, "shares", False),
        ("ardent_ownership_first_close_pct", "D9", TOL_PCT_4DP, "pct", True),
        ("ardent_shares_full", "D4", TOL_SHARES, "shares", False),
        ("fd_fully_funded", "D4", TOL_SHARES, "shares", False),
        ("ardent_ownership_full_pct", "D9", TOL_PCT_4DP, "pct", True),
        ("pool_pct_post_t1", "D3", TOL_PCT_4DP, "pct", True),
        ("effective_pre_money", "D3", TOL_USD_CENT, "usd", False),
        ("headline_post_money", "D3", TOL_USD_CENT, "usd", False),
    )
    for field, dim, tol, kind, star in scalar_specs:
        _register(no, dim, star, f"economics {field}", "economics.json", field, "equals", f"economics.{field}", _value_check(field, field, tol, kind))
        no += 1

    expected = load_expected()
    for index, row in enumerate(expected["conversions"]):
        for field, tol, kind, star in (
            ("amount", TOL_USD_CENT, "usd", False),
            ("cap_basis", TOL_SHARES, "exact", True),
            ("cap_price", TOL_PPS_6DP, "pps", False),
            ("binding_price", TOL_PPS_6DP, "pps", False),
            ("binding_basis", TOL_SHARES, "exact", True),
            ("shares", TOL_SHARES, "shares", False),
            ("mfn_applied", TOL_SHARES, "exact", True),
            ("mfn_source", TOL_SHARES, "exact", True),
        ):
            # Output is addressed by instrument identifier, expectations by position in
            # the answer key. Keeping the two apart is what removes the dependency on an
            # unpublished row order.
            output_path = f"conversions.{row['key']}.{field}"
            expected_path = f"conversions.{index}.{field}"
            evaluator = (
                _exact_check(output_path, expected_path) if kind == "exact"
                else _value_check(output_path, expected_path, tol, kind)
            )
            _register(no, "D2", star, f"conversion {row['key']} {field}", "instrument_register.csv", f"{row['key']}.{field}", "equals", f"conversion.{row['key']}.{field}", evaluator)
            no += 1

    # Retired 2026-08-11, same defect class as D6 but a narrower blast radius: these
    # twelve values depend on the *magnitude* of the Series Seed and Series A Original
    # Issue Prices, and those figures are in none of the 336 delivered corpus files. The
    # charter defines the Original Issue Price as "the price per share at which shares of
    # that series were first issued, as stated in the purchase agreement under which they
    # were issued" -- and no Seed or Series A purchase agreement is delivered.
    #
    # The membership of this set is not a judgment call, it is measured: move `oip` and
    # `conversion_price` together for both series (together, because the charter states
    # the conversion price is initially the Original Issue Price, so the as-converted
    # ratio stays 1:1 and every share count an attempt can actually derive is unaffected)
    # and regenerate the expectations. Exactly these twelve move. `tools/check_derivable.py`
    # re-runs that perturbation as a gate, so a position that starts depending on an
    # unstated figure fails the build instead of quietly becoming unreachable.
    #
    # Note which rows these are: only exit values 2 and 3, where liquidation preference
    # governs the split. Rows 0 and 1 are reachable and stay scored -- trial 06 got them
    # wrong on its own merits, which is exactly the discrimination the case is for.
    UNREACHABLE_PATHS = frozenset({
        "waterfall_grid.modelled.2.residual_to_common",
        "waterfall_grid.modelled.3.residual_to_common",
        "waterfall_grid.consentable.2.residual_to_common",
        "waterfall_grid.consentable.3.residual_to_common",
        "returns.2.moic_round",
        "returns.2.moic_fund",
        "returns.2.gross_xirr_reported",
        "returns.2.net_xirr",
        "returns.3.moic_round",
        "returns.3.moic_fund",
        "returns.3.gross_xirr_reported",
        "returns.3.net_xirr",
    })

    for branch in ("modelled", "consentable"):
        for index, _row in enumerate(expected["waterfall_grid"][branch]):
            for field, tol, kind, star in (
                ("exit_value", TOL_USD_CENT, "usd", False),
                ("carve_out", TOL_USD_CENT, "usd", False),
                ("residual_to_common", TOL_USD_CENT, "usd", False),
                ("total_distributed", TOL_USD_CENT, "usd", False),
                ("elections", TOL_SHARES, "exact", True),
                ("capped_series", TOL_SHARES, "exact", True),
            ):
                path = f"waterfall_grid.{branch}.{index}.{field}"
                if path in UNREACHABLE_PATHS:
                    continue
                evaluator = _exact_check(path, path) if kind == "exact" else _value_check(path, path, tol, kind)
                _register(no, "D5", star, f"waterfall {branch} row {index} {field}", "capitalization.xlsx", path, "equals", path, evaluator)
                no += 1

    for index, _row in enumerate(expected["returns"]):
        for field, tol, kind in (
            ("exit_value", TOL_USD_CENT, "usd"),
            ("moic_round", TOL_RATIO, "ratio"),
            ("moic_fund", TOL_RATIO, "ratio"),
            ("gross_xirr_reported", TOL_IRR_4DP, "irr"),
            ("net_xirr", TOL_IRR_4DP, "irr"),
        ):
            path = f"returns.{index}.{field}"
            if path in UNREACHABLE_PATHS:
                continue
            _register(no, "D7", False, f"returns row {index} {field}", "capitalization.xlsx", path, "equals", path, _value_check(path, path, tol, kind))
            no += 1

    # The D6 down-round block that stood here is retired; see the note in DIMENSIONS.

    def substantive(deliverable: str, predicate: Callable[[Any], bool], detail: str) -> Callable:
        def evaluate(output: dict[str, Any], _expected: dict[str, Any]) -> tuple[bool, str, str]:
            artifact = output.get("_artifacts", {}).get(deliverable)
            if artifact is None:
                return False, "missing", deliverable
            ok = bool(predicate(artifact))
            return ok, "pass" if ok else "wrong", detail
        return evaluate

    def rows_present(deliverable: str) -> Callable:
        return substantive(
            deliverable,
            lambda a: isinstance(a, list) and len(a) >= 1,
            "at least one row required",
        )

    def every_row_has(deliverable: str, field: str) -> Callable:
        return substantive(
            deliverable,
            lambda a: isinstance(a, list)
            and len(a) >= 1
            and all(row.get(field) not in (None, "") for row in a),
            f"every row must carry {field}",
        )

    def workbook_carries(field: str, tol: Decimal) -> Callable:
        """The workbook must report a governing quantity as a cached numeric value."""
        def evaluate(output: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, str, str]:
            artifact = output.get("_artifacts", {}).get("capitalization.xlsx")
            if not isinstance(artifact, dict):
                return False, "missing", "capitalization.xlsx"
            want = expected[field]
            ok = any(eq(got, want, tol=tol, kind="number") for got in artifact.get("numbers", ()))
            return ok, "pass" if ok else "wrong", f"no cached cell ties out to {field}"
        return evaluate

    def sheet_has(field: str, minimum: int, detail: str) -> Callable:
        def build(sheet_name: str) -> Callable:
            return substantive(
                "capitalization.xlsx",
                lambda a: isinstance(a, dict)
                and isinstance(a.get("sheets"), dict)
                and a["sheets"].get(sheet_name, {}).get(field, 0) >= minimum,
                f"{sheet_name} {detail}",
            )
        return build

    # Registers and structured deliverables were single all-or-nothing positions, so a
    # register missing one column read the same as one missing every column, and seven
    # dimensions could only ever report 0 or 1. Each obligation the schema documents
    # states is now its own position. The set of obligations is unchanged, and so is the
    # G2 star set: splitting a starred conjunction yields starred parts.
    structural: list[tuple[str, str, str, bool, Callable]] = [
        ("D1", "term_sheet_extract.json", "version_arbitration_object", True,
         substantive("term_sheet_extract.json", lambda a: isinstance(a, dict) and isinstance(a.get("version_arbitration"), dict), "version arbitration object required")),
        ("D1", "term_sheet_extract.json", "governing_version", True,
         substantive("term_sheet_extract.json", lambda a: isinstance(a, dict) and isinstance(a.get("version_arbitration"), dict) and bool(a["version_arbitration"].get("governing")), "governing version must be named")),
        ("D8", "deviation_register.csv", "rows_present", False, rows_present("deviation_register.csv")),
    ]
    # Column names below are the ones `00_task/output_schemas/` publishes. They used to be
    # the answer key's shorter internal spellings -- `term`, `item`, `source_a`, `source_b`,
    # `position`, `fallback` -- which appear in no schema document, so a submission
    # following the published column order failed a presence check on columns it had
    # correctly supplied under their published names. Three of those are core items.
    for field in ("term_id", "severity", "direction", "usd_impact", "evidence_file", "evidence_section"):
        structural.append(("D8", "deviation_register.csv", field, False, every_row_has("deviation_register.csv", field)))
    structural += [
        # `gate_results` and `remedies` are the published key names, and the schema
        # publishes both as arrays. This used to require top-level `gate` and `remedy`
        # *objects*: neither the names nor the types appear in
        # `00_task/output_schemas/ic_recommendation.json.md`, and both are core items, so
        # G2 stayed unpassable even after the register vocabulary was aligned. Trial 07
        # wrote exactly the six published keys in the published order and failed both.
        ("D9", "ic_recommendation.json", "gate_results", True,
         substantive("ic_recommendation.json", lambda a: isinstance(a, dict) and isinstance(a.get("gate_results"), list) and bool(a["gate_results"]), "a non-empty gate_results array is required")),
        ("D9", "ic_recommendation.json", "remedies", True,
         substantive("ic_recommendation.json", lambda a: isinstance(a, dict) and isinstance(a.get("remedies"), list) and bool(a["remedies"]), "a non-empty remedies array is required")),
        ("D10", "negotiation_memo.pdf", "memo_present", False,
         substantive("negotiation_memo.pdf", lambda a: isinstance(a, dict) and a.get("size", 0) >= 32, "a non-empty memo is required")),
        ("D10", "negotiation_memo.pdf", "searchable_heading", False,
         substantive("negotiation_memo.pdf", lambda a: isinstance(a, dict) and "TRANSACTION RECOMMENDATION" in a.get("text", ""), "searchable text layer with the required heading")),
        ("D11", "reconciliation.csv", "rows_present", True, rows_present("reconciliation.csv")),
    ]
    for field in ("item_id", "source_a_file", "source_b_file", "governing_source", "governing_value", "downstream_use"):
        structural.append(("D11", "reconciliation.csv", field, True, every_row_has("reconciliation.csv", field)))
    # Cross-file tie-out of the workbook itself. Diagnostic rather than starred: the
    # workbook's pass bar is the structural contract below, and these read out how much
    # of the model an agent actually built.
    for field, tol in (
        ("base_shares", TOL_SHARES),
        ("convertible_shares_total", TOL_SHARES),
        ("pool_increase", TOL_SHARES),
        ("pre_money_fd_Q", TOL_SHARES),
        ("pps", TOL_PPS_6DP),
        ("round_shares_t1", TOL_SHARES),
        ("ardent_shares_t1", TOL_SHARES),
        ("fd_post_first_close", TOL_SHARES),
        ("fd_fully_funded", TOL_SHARES),
    ):
        structural.append(("D11", "capitalization.xlsx", f"tie_out_{field}", False, workbook_carries(field, tol)))
    structural += [
        ("D12", "capitalization.xlsx", "sheet_contract", True,
         substantive("capitalization.xlsx", lambda a: isinstance(a, dict) and a.get("sheet_names") == list(SHEET_NAMES), "the nine required sheets, in order, exactly named")),
        ("D12", "capitalization.xlsx", "formula_provenance", True,
         substantive("capitalization.xlsx", lambda a: isinstance(a, dict) and isinstance(a.get("sheets"), dict) and bool(a["sheets"]) and all(facts.get("calculated_count", 0) >= 1 for facts in a["sheets"].values()), "every sheet needs a formula carrying its cached value")),
    ]
    calculated_on = sheet_has("calculated_count", 1, "needs a live formula with a cached value")
    populated = sheet_has("rows", 1, "must not be empty")
    labelled = sheet_has("header_labels", 2, "needs a labelled header row")
    for sheet_name in SHEET_NAMES:
        structural.append(("D12", "capitalization.xlsx", f"{sheet_name}.calculated", False, calculated_on(sheet_name)))
        structural.append(("D13", "capitalization.xlsx", f"{sheet_name}.populated", False, populated(sheet_name)))
        structural.append(("D13", "capitalization.xlsx", f"{sheet_name}.header_row", False, labelled(sheet_name)))
    structural.append(("D13", "redline_positions.csv", "rows_present", False, rows_present("redline_positions.csv")))
    for field in ("section", "proposed_position", "fallback_position", "priority_rank", "concede"):
        structural.append(("D13", "redline_positions.csv", field, False, every_row_has("redline_positions.csv", field)))
    structural.append(
        ("D14", "deviation_register.csv", "unused_enums_absent", False,
         substantive("deviation_register.csv", lambda a: isinstance(a, list) and all(row.get("severity") not in {"immaterial", "critical", "requires_lp_consent"} for row in a), "unused severity values must be absent")),
    )
    for dim, deliverable, field, star, evaluator in structural:
        _register(no, dim, star, f"{deliverable} {field}", deliverable, field, "substantive", f"{deliverable}.{field}", evaluator)
        no += 1

    global ALL_STARS
    ALL_STARS = frozenset(check.no for check in CHECKS if check.starred)


SHEET_NAMES = (
    "01_PreFinancing", "02_Convertibles", "03_PoolSolve", "04_PostFinancing",
    "05_Waterfall", "06_AntiDilution", "07_Returns", "08_Sensitivity", "09_TieOut",
)


def load_expected(expected_path: Path = EXPECTED_PATH) -> dict[str, Any]:
    return json.loads(Path(expected_path).read_text(encoding="utf-8"))


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


# `instrument_register.csv` is scored through the column names the agent-facing schema
# actually publishes. Until 2026-08-11 the grader read `amount`, `cap_basis`,
# `mfn_applied` and `mfn_source` -- the answer key's internal spellings, which appear
# nowhere in `00_task/output_schemas/instrument_register.csv.md`. Three of those four are
# core items across eight instruments, so twenty-four core positions were unreachable and
# **G2 could not be passed by any submission that followed the published schema**. Blind
# trial 06's register header was byte-identical to the published column list and scored
# zero on all of them.
PUBLISHED_REGISTER_COLUMNS = {
    "principal_or_purchase_amount": "amount",
    "cap_basis_code": "cap_basis",
    "special_terms_applied": "mfn_applied",
    "special_terms_source": "mfn_source",
}


def _strip_document_extension(value: str) -> str:
    """Drop a trailing document extension from an instrument identifier.

    The identifier is carried by the instrument's own filename as well as by the register
    that lists the instruments, so `safe_kadence.pdf` names the right instrument and
    differs only by the extension it was read from. Rejecting it would fail eight
    positions on a transcription detail.
    """
    return re.sub(r"\.(pdf|docx?|xlsx?|csv|txt)$", "", value)


def _conversions_by_instrument(rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """Key submitted register rows by the instrument identifier the corpus records.

    Rows used to be matched by **position**: `conversions.{index}`, against an order that
    came from `tools/facts.py` and was published nowhere. Trial 06 wrote its notes before
    its SAFEs, which is a perfectly ordinary arrangement, so every per-instrument value it
    reported was compared against a different instrument. That made the D2 numbers for all
    six trials meaningless rather than merely low.

    `instrument_id` is derivable: each convertible is delivered as
    `03_convertibles/<instrument_id>.pdf`, and `CFO_Instrument_Register.xlsx` carries an
    `instrument_id` column holding exactly those identifiers. Matching on it makes row
    order irrelevant, which is now stated in the schema, and lets a submission carry extra
    rows -- warrants belong in this register too and are scored elsewhere.
    """
    by_instrument: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = str(row.get("instrument_id", "") or "").strip().lower()
        # The identifier is carried by the instrument's own filename and by the register
        # that lists the instruments, so `safe_kadence.pdf` is a near-miss worth accepting:
        # it names the right instrument and differs only by the extension it was read from.
        # Rejecting it would fail eight positions on a transcription detail.
        identity = _strip_document_extension(identity)
        if not identity:
            continue
        scored: dict[str, Any] = dict(row)
        for published, internal in PUBLISHED_REGISTER_COLUMNS.items():
            if published in row:
                scored[internal] = row[published]
        scored["mfn_applied"] = str(scored.get("mfn_applied", "")).strip().lower() == "true"
        # `special_terms_source` names an instrument, so it gets the same treatment as
        # `instrument_id`: the two must normalise identically or a submission that spells
        # the identifier one way in both columns passes one check and fails the other.
        source = _strip_document_extension(str(scored.get("mfn_source", "") or "").strip().lower())
        scored["mfn_source"] = source or None
        for field in ("binding_basis", "cap_basis"):
            if field in scored and scored[field] is not None:
                scored[field] = str(scored[field]).strip()
        # First row wins: the schema requires one row per instrument, and silently
        # preferring a later duplicate would let a submission bury a wrong answer.
        by_instrument.setdefault(identity, scored)
    return by_instrument


SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
# `r:id` on a <sheet> lives in the officeDocument relationships namespace; the
# relationship parts themselves live in the package namespace. They are not the same URI.
DOC_RELS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XLSX_REQUIRED_PARTS = (
    "[Content_Types].xml",
    "_rels/.rels",
    "xl/workbook.xml",
    "xl/_rels/workbook.xml.rels",
)


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.iter(f"{{{SHEET_NS}}}t"))
        for item in root.findall(f"{{{SHEET_NS}}}si")
    ]


def _cell_text(cell: ElementTree.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        inline = cell.find(f"{{{SHEET_NS}}}is")
        return "" if inline is None else "".join(n.text or "" for n in inline.iter(f"{{{SHEET_NS}}}t"))
    value = cell.find(f"{{{SHEET_NS}}}v")
    text = (value.text or "") if value is not None else ""
    if kind == "s":
        try:
            return shared[int(text)]
        except (ValueError, IndexError):
            return ""
    return text


def _worksheet_facts(sheet: ElementTree.Element, shared: list[str]) -> dict[str, Any]:
    """Header row, calculated cells and cached numbers of one worksheet.

    A calculated cell is a cell carrying both a live formula and its cached numeric
    result. Counting `<f>` tags alone is what let a two-hundred-byte stub with a single
    `1+1` stand in for a nine-schedule workbook.
    """
    rows = sheet.findall(f"{{{SHEET_NS}}}sheetData/{{{SHEET_NS}}}row")
    labels = 0
    if rows:
        for cell in rows[0].findall(f"{{{SHEET_NS}}}c"):
            text = _cell_text(cell, shared).strip()
            if text and number(text) is None:
                labels += 1
    formulas = 0
    calculated = 0
    numbers: list[str] = []
    for row in rows:
        for cell in row.findall(f"{{{SHEET_NS}}}c"):
            formula = cell.find(f"{{{SHEET_NS}}}f")
            cached = cell.find(f"{{{SHEET_NS}}}v")
            cached_number = None
            if cached is not None and cell.get("t") not in {"s", "inlineStr", "str", "b", "e"}:
                cached_number = number(cached.text)
            if formula is not None:
                formulas += 1
                if cached_number is not None:
                    calculated += 1
            if cached_number is not None:
                numbers.append(format(cached_number, "f"))
    return {
        "rows": len(rows),
        "header_labels": labels,
        "formula_count": formulas,
        "calculated_count": calculated,
        "numbers": numbers,
    }


def _xlsx_metadata(path: Path) -> dict[str, Any]:
    """Read the workbook as a real OOXML package, refusing malformed ones.

    `zipfile` will happily read `xl/workbook.xml` out of an archive that no spreadsheet
    application can open. G0 claims the deliverables are parseable, so a package that
    lacks its content types or relationships, or that declares a sheet with no worksheet
    part behind it, is reported as malformed rather than measured.
    """
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        missing = [part for part in XLSX_REQUIRED_PARTS if part not in names]
        if missing:
            raise ValueError("not an OOXML package, missing " + ", ".join(missing))
        book = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        rels = {
            node.get("Id"): (node.get("Target") or "")
            for node in ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        }
        shared = _shared_strings(archive)
        declared = [
            (node.get("name") or "", node.get(f"{{{DOC_RELS_NS}}}id") or "")
            for node in book.findall(f"{{{SHEET_NS}}}sheets/{{{SHEET_NS}}}sheet")
        ]
        sheets: dict[str, Any] = {}
        unresolved: list[str] = []
        for sheet_name, rel_id in declared:
            target = rels.get(rel_id, "")
            # Targets are relative to xl/ unless absolute; writers vary on the "./" prefix.
            relative = target[2:] if target.startswith("./") else target
            part = relative[1:] if relative.startswith("/") else f"xl/{relative}"
            if not relative or part not in names:
                unresolved.append(sheet_name)
                continue
            sheets[sheet_name] = _worksheet_facts(
                ElementTree.fromstring(archive.read(part)), shared
            )
        if unresolved:
            raise ValueError("sheets declared with no worksheet part: " + ", ".join(unresolved))
    return {
        "sheet_names": [name for name, _ in declared],
        "sheets": sheets,
        "formula_count": sum(facts["formula_count"] for facts in sheets.values()),
        "numbers": [value for facts in sheets.values() for value in facts["numbers"]],
    }


def _pdf_text(path: Path) -> str:
    """Extract the text layer, distinguishing a missing reader from an unreadable PDF.

    Only the second is the submission's problem. The first is an environment defect and
    `require_environment` has already refused to score without the reader.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - require_environment runs first
        raise EnvironmentContractError(f"pypdf is required to read {path.name}") from exc
    try:
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    except Exception:
        return ""


def _load_output(output_dir: Path) -> tuple[dict[str, Any], list[str]]:
    output: dict[str, Any] = {"_artifacts": {}}
    errors: list[str] = []
    for name in REQUIRED_ARTIFACTS:
        path = output_dir / name
        if not path.is_file():
            errors.append(f"missing:{name}")
            continue
        try:
            if name.endswith(".json"):
                value = _load_json(path)
            elif name.endswith(".csv"):
                value = _load_csv(path)
            elif name.endswith(".xlsx"):
                value = _xlsx_metadata(path)
            elif name.endswith(".pdf"):
                value = {"size": path.stat().st_size, "text": _pdf_text(path)}
            else:
                value = {"size": path.stat().st_size}
            output["_artifacts"][name] = value
            if name == "economics.json" and isinstance(value, dict):
                output.update(value)
            if name == "instrument_register.csv" and isinstance(value, list):
                output["conversions"] = _conversions_by_instrument(value)
        except EnvironmentContractError:
            # An environment defect is never a defect of the submission.
            raise
        except Exception as exc:
            errors.append(f"malformed:{name}:{type(exc).__name__}")
    return output, errors


def _gate_g1(output: dict[str, Any]) -> list[str]:
    bad: list[str] = []
    try:
        # Read through the provenance envelope the schema requires; see `_unwrap`.
        if number(_lookup(output, "ardent_ownership_first_close_pct")) is None:
            bad.append("ownership first close is not numeric")
        if number(_lookup(output, "ardent_ownership_full_pct")) is None:
            bad.append("ownership full is not numeric")
    except (KeyError, IndexError, TypeError, ValueError):
        bad.append("economics ownership fields unavailable")
    workbook = output.get("_artifacts", {}).get("capitalization.xlsx", {})
    if workbook and workbook.get("sheet_names") != list(SHEET_NAMES):
        bad.append("capitalization sheet contract mismatch")
    return bad


def score(output_dir: Path, *, expected_path: Path = EXPECTED_PATH) -> dict[str, Any]:
    require_environment()
    _build_registry()
    expected = load_expected(expected_path)
    output, shape_errors = _load_output(output_dir)
    RESULTS.clear()
    for check in CHECKS:
        try:
            ok, kind, detail = check.evaluator(output, expected)
        except Exception as exc:
            ok, kind, detail = False, "malformed", f"{type(exc).__name__}:{exc}"
        RESULTS.append({
            "no": check.no, "dimension": check.dimension, "starred": check.starred,
            "description": check.description, "obligation": check.obligation,
            "pass": bool(ok), "kind": kind, "detail": "" if ok else str(detail)[:220],
        })
    RESULTS.sort(key=lambda row: row["no"])
    passed = sum(1 for row in RESULTS if row["pass"])
    stars_failed = [row["no"] for row in RESULTS if row["starred"] and not row["pass"]]
    g0 = not shape_errors and all(name in output["_artifacts"] for name in REQUIRED_ARTIFACTS)
    g1_bad = _gate_g1(output) if g0 else ["G0 failed"]
    gates = [
        {"id": "G0", "pass": g0, "detail": sorted(shape_errors)},
        {"id": "G1", "pass": not g1_bad, "detail": sorted(g1_bad)},
        {"id": "G2", "pass": not stars_failed, "detail": stars_failed},
    ]
    raw_score = Decimal(passed) / Decimal(len(RESULTS)) if RESULTS else Decimal("0")
    final_score = raw_score if all(gate["pass"] for gate in gates) else Decimal("0")
    dimensions = {}
    for key, name in DIMENSIONS.items():
        rows = [row for row in RESULTS if row["dimension"] == key]
        dimensions[key] = {"name": name, "passed": sum(r["pass"] for r in rows), "N": len(rows)}
    return {
        "score": format(final_score, "f"), "diagnostic_score": format(raw_score, "f"),
        "passed": passed, "N": len(RESULTS), "gates": gates,
        "starred_passed": len(ALL_STARS) - len(stars_failed), "starred_N": len(ALL_STARS),
        "dimensions": dimensions, "results": list(RESULTS),
    }


def score_empty_output() -> float:
    return float(Decimal(score(Path("__finance001_empty_output__"))["score"]))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--expected", type=Path, default=EXPECTED_PATH)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        report = score(args.output_dir, expected_path=args.expected)
    except EnvironmentContractError as exc:
        # Exit before scoring rather than reporting an environment defect as a low score.
        print(str(exc), file=sys.stderr)
        return 2
    rendered = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.report:
        # `Path.write_text(newline=...)` needs Python 3.10, and on 3.9 it raised a
        # TypeError here - after scoring had finished - which read as a report-writing
        # bug rather than an unmet version requirement. The version contract is enforced
        # up front by `require_environment`; the write itself stays portable.
        with args.report.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
    print(rendered, end="")
    return 0


_build_registry()

if __name__ == "__main__":
    raise SystemExit(main())
