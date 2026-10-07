"""case/scripts/submission_reader.py - read submitted field values from the FOUR
published deliverables (decision_memo.pdf, financial_model.xlsx,
assumptions_and_sources.xlsx, evidence_manifest.csv) and from no file the agent was
never asked to produce (04-04-PLAN.md Task 1).

This module owns EXTRACTION and nothing else. `case/scripts/score_outputs.py` keeps
owning the gate structure and the value-comparison rules; this module's only job is to
answer "what did the submission actually say, and where did it say it".

Why this module exists: `score_outputs._submitted_values()` used to read two JSON
sidecars (`decision_memo.json`, `assumptions_and_sources.json`) that are not in the
published contract - no agent was ever asked to produce them, so a submission that
followed the contract exactly had a ceiling of about 2%. This module reads the real
bytes of the four real deliverables instead, reusing the readers that already existed
and were simply unused: `cross_file.extract_memo_text`, `xlsx_raw.read_workbook` /
`resolve_defined_name`, `recalc.trusted_values`, `cross_file.csv_column_values`.

Routing rule (derived from the two schemas, never invented):
  - Every NUMERIC graded field is a `financial_model.xlsx` named range. Read it there
    first - a named range resolves to one cell, which is deterministic in a way that
    scanning prose is not.
  - Every conclusion / categorical / date / array field is published only in the memo.
    Read it there, anchored on its `x-memo-label`.
  - A field published in BOTH is satisfied by either: this module returns every
    candidate value it found for a name as a tuple, never collapsing to one source, and
    `score_outputs.values_agree` accepts any of them.

Six fields in the vocabulary have no home in either of the two schemas' own top-level
properties: `provenance` (the JSON-object sentinel name PROVENANCE_FIELDS declares for
the envelope container itself - deliberately excluded from
`EVIDENCE_MANIFEST_COLUMN_ORDER` per `field_vocabulary`'s own CSV-flattening rule) and
five of `GAP_ENTRY_FIELDS` (`gap_type`, `blocked_field`, `evidence_checked`,
`what_would_resolve`, `definitive_conclusion_withheld` - `claim_id` already resolves
through `evidence_manifest.csv`, which shares that column name). Both are per-ENTRY
properties of an array (`gap_list`'s rows), not a single memo-wide scalar fact, so
hoisting them into `decision_memo.schema.json` as new top-level properties - the
pattern already used once for `scenario_label` - would inflate `rubric_registry`'s
frozen 174/54 item count, which this plan may not touch (EXPECTED.json's sha256 must
stay byte-identical and no rubric item may be added or dropped). Two narrower routes
close the gap without that:
  - `provenance` is COMPOSITE: satisfied whenever any of the six flattened provenance
    leaf columns resolves to a real value in `evidence_manifest.csv` - the envelope's
    presence is evidenced by its own children's presence, never invented independently.
  - The five orphaned `GAP_ENTRY_FIELDS` are read via `GAP_ENTRY_SUB_LABELS`, an
    informal label convention recognised only within the slice of memo text that
    follows the `gap_list` field's own `x-memo-label` ("Gap List") - a label, per the
    plan's own prescribed remedy, just not one promoted into the graded schema surface.
`tools/check_scoreable.py` documents and enforces both as explicit, narrow route types.

04-07-PLAN.md Task 2 adds two ROW-PRESERVING readers alongside the flat `values` mapping
above, never replacing it (the value path depends on the flat shape and works):
`gap_list_entries()` (one dict per memo gap-list entry actually printed, so a specific
entry's `claim_id` can be paired to a specific manifest row) and `manifest_rows()` (one
dict per `evidence_manifest.csv` data row, in file order). `claim_id` has no
`x-memo-label` in `refusal_gap_list.schema.json` (deliberately - see the module docstring
above), so per-entry extraction anchors on `_GAP_ENTRY_CLAIM_LABEL` ("Claim ID"), an
informal convention this module adds rather than the schema publishes, mirroring the
render convention `tools/submission_fixture.py` already used for its own gap entry before
this plan existed. Every OTHER gap-entry sub-label is paired to the SAME entry by
POSITION - the Nth "Claim ID:" occurrence pairs with the Nth occurrence of each other
sub-label - never by inventing a delimiter the schema does not define.
"""

from __future__ import annotations

import csv
import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import cross_file
import field_vocabulary as fv
import recalc
import tolerances
import xlsx_raw

REPO_ROOT = Path(__file__).resolve().parents[2]
_DECISION_MEMO_SCHEMA_PATH = REPO_ROOT / "00_task" / "output_schemas" / "decision_memo.schema.json"
_REFUSAL_GAP_LIST_SCHEMA_PATH = REPO_ROOT / "00_task" / "output_schemas" / "refusal_gap_list.schema.json"


def _load_gap_entry_labels() -> dict[str, str]:
    """`{GAP_ENTRY_FIELDS name: x-memo-label}` read from refusal_gap_list.schema.json's
    own `items.properties` (04-05-PLAN.md Task 0) - never a private constant, so a label
    can never drift from the contract `00_task/output_schemas/` actually publishes.
    `refusal_gap_list.schema.json` IS agent-visible (it ships in the probe workspace via
    `tools.probe_workspace.SCHEMAS_REL`) and already declares exactly these properties;
    this reads the labels from it rather than restating them a second time here.
    `claim_id` is deliberately absent from the result: it resolves through
    `evidence_manifest.csv`'s own column of the same name (see module docstring), never
    through a memo sub-label. Never raises: an unreadable or malformed schema yields an
    empty mapping - a loud, gradeable failure downstream, never a scorer crash."""
    try:
        schema = json.loads(_REFUSAL_GAP_LIST_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    props = ((schema.get("items") or {}).get("properties")) or {}
    labels: dict[str, str] = {}
    for name, spec in props.items():
        label = spec.get("x-memo-label") if isinstance(spec, dict) else None
        if label:
            labels[name] = label
    return labels


# --- Informal gap-entry sub-labels (see module docstring). Never decision_memo.schema.json
# properties - see the docstring for why these five stay out of the graded schema surface.
# Read from refusal_gap_list.schema.json's own x-memo-label annotations (04-05-PLAN.md Task 0),
# never a hardcoded literal here - the schema is the single source of truth for the label text. ---
GAP_ENTRY_SUB_LABELS: dict[str, str] = _load_gap_entry_labels()

# 04-07-PLAN.md Task 2: the informal render label for a gap entry's own `claim_id` - the
# one GAP_ENTRY_FIELDS property `refusal_gap_list.schema.json` publishes no
# `x-memo-label` for at all (see module docstring). "Claim ID" is not invented here: it is
# the exact string `tools/submission_fixture.py`'s own gap-entry builder already prints
# ("Claim ID: {claim}"), predating this plan - this constant makes that existing render
# convention a real extraction anchor instead of leaving it unread.
_GAP_ENTRY_CLAIM_LABEL = "Claim ID"

# The six PROVENANCE_FIELDS leaves that are real evidence_manifest.csv columns (per
# field_vocabulary's CSV-flattening rule). "provenance" itself is the one name excluded
# from that flattening - its composite route is derived from these six, never invented.
_PROVENANCE_LEAF_NAMES: tuple[str, ...] = tuple(
    f.name for f in fv.PROVENANCE_FIELDS if f.name != "provenance"
)


def _load_memo_labels() -> dict[str, str]:
    """`{published field name: x-memo-label}` read from decision_memo.schema.json itself,
    so a label can never drift from the contract the agent is actually shown. Never
    raises: an unreadable or malformed schema yields an empty mapping, which simply
    means no memo field resolves - a loud, gradeable failure, never a scorer crash."""
    try:
        schema = json.loads(_DECISION_MEMO_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    labels: dict[str, str] = {}
    for name, spec in (schema.get("properties") or {}).items():
        label = spec.get("x-memo-label") if isinstance(spec, dict) else None
        if label:
            labels[name] = label
    return labels


MEMO_LABELS: dict[str, str] = _load_memo_labels()

_FIELD_KIND: dict[str, str] = {s.name: s.kind for s in fv.DECISION_MEMO_FIELDS}
_CELL_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")


def _empty_workbook_facts() -> "xlsx_raw.WorkbookFacts":
    """A zero-content placeholder - never `None` - for `SubmissionValues.model_facts`
    when the real `financial_model.xlsx` could not be read at all (04-05-PLAN.md Task 2:
    lets `cross_file.agrees_across` call `.cells` unconditionally without a caller-side
    null check)."""
    return xlsx_raw.WorkbookFacts(
        path=Path(), sheets=(), cells={}, defined_names={}, shared_strings=()
    )


@dataclass(frozen=True)
class SubmissionValues:
    """Everything this module could extract from one submitted output directory.

    `values` maps a published field name to a tuple of every candidate raw value found
    for it, across every deliverable that could carry it - never collapsed to one
    source, so "satisfied by either" (the routing rule) is representable. An empty
    tuple means the field was not found anywhere; that is a normal, gradeable outcome,
    never an exception.

    `errors` carries every named structural condition encountered while reading -
    a missing file, an unresolved named range, a recalculation fallback, a missing CSV
    column - never raised, always recorded (SCORE-05's never-raise boundary).

    `model_facts`/`model_trusted` (04-05-PLAN.md Task 2): the SAME `financial_model.xlsx`
    `WorkbookFacts` (and whether it came from a trusted `recalc.trusted_values` pass)
    already read for `values` above, exposed a second time so `score_outputs.py` can call
    `cross_file.agrees_across` for `CROSS_FILE_TIE` items without a second, duplicate
    read of the same file. Never `None` - an empty placeholder
    (`xlsx_raw.WorkbookFacts(path=..., sheets=(), cells={}, defined_names={},
    shared_strings=())`) stands in when the real file could not be read, so a caller
    never has to null-check before passing it to a function that unconditionally reads
    `.cells`.

    `gap_entries`/`manifest_row_records` (04-07-PLAN.md Task 2): the ROW-PRESERVING
    counterparts to `values` for the two array-shaped deliverables where per-entry
    linkage matters - `gap_list_entries(memo_text)` and `manifest_rows(manifest_path)`
    respectively, never reconstructed from the flat mapping (which already discarded
    that structure). `()` when the respective source could not be read or carried no
    entries - never `None`, never raises.
    """

    values: dict[str, tuple]
    errors: tuple[str, ...]
    memo_text: "str | None" = None
    model_facts: "xlsx_raw.WorkbookFacts" = field(default_factory=_empty_workbook_facts)
    model_trusted: bool = False
    gap_entries: tuple[dict[str, str], ...] = ()
    manifest_row_records: tuple[dict[str, str], ...] = ()


def _col_letter(index: int) -> str:
    return chr(ord("A") + index)


# ---------------------------------------------------------------------------
# decision_memo.pdf - label-anchored text extraction
# ---------------------------------------------------------------------------

_LABEL_SEPARATORS = ":-–— \t"


def _find_label_value(text: str, label: str) -> "str | None":
    """The value printed after `label` in `text`: the remainder of the same line if
    non-empty after stripping the label and any separator punctuation, else the next
    non-empty line. `None` if the label never appears. Deliberately loose-location: the
    label may appear anywhere in a line (a bullet, a number prefix, a table cell), never
    only at line-start - strict-VALUE is enforced later, in the Decimal/string
    comparison, never here."""
    if not text or not label:
        return None
    label_cf = " ".join(label.split()).casefold()
    lines = text.splitlines()
    collapsed = [" ".join(line.split()) for line in lines]
    for i, line in enumerate(collapsed):
        idx = line.casefold().find(label_cf)
        if idx == -1:
            continue
        remainder = line[idx + len(label_cf):].strip(_LABEL_SEPARATORS).strip()
        if remainder:
            return remainder
        for candidate in collapsed[i + 1:i + 4]:
            candidate = candidate.strip()
            if candidate:
                return candidate
        return None
    return None


def find_all_label_values(text: "str | None", label: str) -> tuple[str, ...]:
    """Every value printed after `label` in `text` - one entry per OCCURRENCE of the label, not
    just the first (`_find_label_value`'s single-match behaviour is what every OTHER caller in
    this module wants; this exists only for `score_outputs`'s `EXCLUSIVE` contradiction check,
    04-06-PLAN.md Task 1). Same same-line/next-non-empty-line rule as `_find_label_value`, applied
    independently at each occurrence. `()` if the label never appears or either argument is empty
    - never raises."""
    if not text or not label:
        return ()
    label_cf = " ".join(label.split()).casefold()
    lines = text.splitlines()
    collapsed = [" ".join(line.split()) for line in lines]
    found: list[str] = []
    for i, line in enumerate(collapsed):
        idx = line.casefold().find(label_cf)
        if idx == -1:
            continue
        remainder = line[idx + len(label_cf):].strip(_LABEL_SEPARATORS).strip()
        if remainder:
            found.append(remainder)
            continue
        for candidate in collapsed[i + 1:i + 4]:
            candidate = candidate.strip()
            if candidate:
                found.append(candidate)
                break
    return tuple(found)


def _slice_from_label(text: str, label: str) -> "str | None":
    """Every line from the first occurrence of `label` onward, inclusive - the region a
    per-entry sub-label search is scoped to. `None` if the label never appears."""
    if not text or not label:
        return None
    label_cf = " ".join(label.split()).casefold()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if label_cf in " ".join(line.split()).casefold():
            return "\n".join(lines[i:])
    return None


def memo_labelled_values(memo_text: "str | None", names: "tuple[str, ...] | None" = None) -> dict[str, tuple]:
    """`{published field name: (candidate,...)}` for every name in `names` (default: every
    `DECISION_MEMO_FIELDS` name plus the informal gap-entry sub-labels), anchored on its
    `x-memo-label` (or, for the five gap-entry names, `GAP_ENTRY_SUB_LABELS`). Never
    raises - a `None` memo_text or an unmatched label simply yields no candidate for
    that name."""
    values: dict[str, tuple] = {}
    if memo_text is None:
        return values

    target_names = names if names is not None else tuple(f.name for f in fv.DECISION_MEMO_FIELDS)
    for name in target_names:
        label = MEMO_LABELS.get(name)
        if not label:
            continue
        found = _find_label_value(memo_text, label)
        if found is None:
            continue
        kind = _FIELD_KIND.get(name)
        if kind == "number":
            candidates = cross_file.numeric_candidates(found)
            if candidates:
                values[name] = (candidates[0],)
        else:
            values[name] = (found,)

    # Informal gap-entry sub-labels, scoped to the text from the "Gap List" label onward.
    gap_list_label = MEMO_LABELS.get("gap_list")
    gap_block = _slice_from_label(memo_text, gap_list_label) if gap_list_label else None
    for gap_name, sub_label in GAP_ENTRY_SUB_LABELS.items():
        found = _find_label_value(gap_block or "", sub_label)
        if found:
            values[gap_name] = (found,)
    return values


def gap_list_entries(memo_text: "str | None") -> tuple[dict[str, str], ...]:
    """One `{GAP_ENTRY_FIELDS name: value}` dict PER gap-list entry actually printed in
    the memo, in the order printed - the row-preserving counterpart to
    `memo_labelled_values`'s flat (first-occurrence-only) aggregation (04-07-PLAN.md
    Task 2).

    Built by ZIPPING every sub-label's occurrences by POSITION, scoped to the text from
    the "Gap List" label onward: the entry's `claim_id` (`_GAP_ENTRY_CLAIM_LABEL`, this
    module's own informal convention - see module docstring) anchors how many entries
    exist; each OTHER sub-label's Nth occurrence is paired to the Nth "Claim ID:"
    occurrence. An entry missing one of the five sub-field occurrences simply has that
    key absent from its own dict, never guessed or padded from a neighbouring entry.

    `()` when the memo has no gap-list block, or the block has no extractable claim_id
    occurrence at all (including a submission with a genuinely empty gap list) - never
    raises.
    """
    if memo_text is None:
        return ()
    gap_list_label = MEMO_LABELS.get("gap_list")
    if not gap_list_label:
        return ()
    block = _slice_from_label(memo_text, gap_list_label)
    if block is None:
        return ()

    claim_ids = find_all_label_values(block, _GAP_ENTRY_CLAIM_LABEL)
    if not claim_ids:
        return ()

    sub_label_values: dict[str, tuple[str, ...]] = {
        gap_name: find_all_label_values(block, sub_label)
        for gap_name, sub_label in GAP_ENTRY_SUB_LABELS.items()
    }

    entries: list[dict[str, str]] = []
    for i, claim_id in enumerate(claim_ids):
        entry: dict[str, str] = {"claim_id": claim_id}
        for gap_name, occurrences in sub_label_values.items():
            if i < len(occurrences):
                entry[gap_name] = occurrences[i]
        entries.append(entry)
    return tuple(entries)


# ---------------------------------------------------------------------------
# financial_model.xlsx - named range -> cell -> value
# ---------------------------------------------------------------------------


def model_named_range_values(
    facts: "xlsx_raw.WorkbookFacts | None", names: "tuple[str, ...] | None" = None
) -> dict[str, tuple]:
    """`{published field name: (candidate,...)}` for every name in `names` (default:
    every `FINANCIAL_MODEL_NAMED_RANGES` name), each resolved through
    `xlsx_raw.resolve_defined_name` then read as `value_text`. Never raises - `facts`
    may be `None` (the model was unreadable) and a name that fails to resolve simply
    yields no candidate."""
    values: dict[str, tuple] = {}
    if facts is None:
        return values

    specs = fv.FINANCIAL_MODEL_NAMED_RANGES
    target_names = set(names) if names is not None else {s.name for s in specs}
    kind_of = {s.name: s.kind for s in specs}

    for name in target_names:
        target = xlsx_raw.resolve_defined_name(facts, name)
        if target is None:
            continue
        cell = facts.cells.get(target)
        if cell is None or cell.value_text is None:
            continue
        text = cell.value_text.strip()
        if not text:
            continue
        if kind_of.get(name) == "number":
            number = tolerances.number(text)
            if number is not None:
                values[name] = (number,)
        else:
            values[name] = (text,)
    return values


# ---------------------------------------------------------------------------
# assumptions_and_sources.xlsx - header row -> column -> per-row values
# ---------------------------------------------------------------------------


def _header_row(facts: xlsx_raw.WorkbookFacts) -> dict[str, str]:
    """`{cell text (casefolded): column letter}` for row 1 of the FIRST sheet - never
    raises on a sheet with no header or an empty workbook."""
    header: dict[str, str] = {}
    if not facts.sheets:
        return header
    first_sheet = facts.sheets[0]
    for (sheet, ref), cell in facts.cells.items():
        if sheet != first_sheet:
            continue
        match = _CELL_REF_RE.match(ref)
        if match is None or match.group(2) != "1":
            continue
        if cell.value_text:
            header[cell.value_text.strip().casefold()] = match.group(1)
    return header


_MAX_DATA_ROWS = 500


def _first_nonempty_in_column(facts: xlsx_raw.WorkbookFacts, sheet: str, col: str) -> "str | None":
    """The first non-empty cell text in `col`, scanning from row 2 (row 1 is the
    header) through `_MAX_DATA_ROWS` - a fixed bound so a hostile/huge submission
    cannot make this loop unbounded."""
    for row in range(2, _MAX_DATA_ROWS + 1):
        cell = facts.cells.get((sheet, f"{col}{row}"))
        if cell is not None and cell.value_text and cell.value_text.strip():
            return cell.value_text.strip()
    return None


def assumptions_column_values(facts: "xlsx_raw.WorkbookFacts | None") -> dict[str, tuple]:
    """`{published field name: (candidate,...)}` for every `ASSUMPTIONS_FIELDS` name,
    read from its own header column. Never raises."""
    values: dict[str, tuple] = {}
    if facts is None or not facts.sheets:
        return values
    header = _header_row(facts)
    sheet = facts.sheets[0]
    for spec in fv.ASSUMPTIONS_FIELDS:
        col = header.get(spec.name.casefold())
        if col is None:
            continue
        found = _first_nonempty_in_column(facts, sheet, col)
        if found:
            values[spec.name] = (found,)
    return values


# ---------------------------------------------------------------------------
# evidence_manifest.csv - published column order
# ---------------------------------------------------------------------------


def manifest_column_values(manifest_path: Path) -> tuple[dict[str, tuple], tuple[str, ...]]:
    """`({published field name: (candidate,...)}, errors)` for every
    `EVIDENCE_MANIFEST_COLUMN_ORDER` column (which already flattens six of the seven
    `PROVENANCE_FIELDS` leaves - see `field_vocabulary`'s CSV-flattening rule). Never
    raises - `cross_file.csv_column_values` is itself a never-raise boundary."""
    values: dict[str, tuple] = {}
    errors: list[str] = []
    for column in fv.EVIDENCE_MANIFEST_COLUMN_ORDER:
        col_values, col_errors = cross_file.csv_column_values(manifest_path, column)
        errors.extend(col_errors)
        nonempty = tuple(v.strip() for v in col_values if v and v.strip())
        if nonempty:
            values[column] = (nonempty[0],)

    # COMPOSITE route for "provenance" (see module docstring): the object sentinel's
    # presence is derived from any of its six flattened leaf columns, never invented.
    if any(name in values for name in _PROVENANCE_LEAF_NAMES):
        values["provenance"] = ("present",)
    return values, tuple(errors)


def manifest_rows(manifest_path: Path) -> tuple[tuple[dict[str, str], ...], tuple[str, ...]]:
    """`(rows, errors)` - every `evidence_manifest.csv` data row as `{column: value}`, in
    file order, the row-preserving counterpart to `manifest_column_values`'s flat
    (first-non-empty-value-only) aggregation (04-07-PLAN.md Task 2). Reads the CSV's own
    real header (whatever it actually is, never assumed to equal
    `EVIDENCE_MANIFEST_COLUMN_ORDER` - a malformed header is a G7/shape concern, not this
    reader's job to enforce). Never raises: an unreadable file yields `((), errors)`.
    """
    try:
        with open(manifest_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = tuple(
                {k: (v or "").strip() for k, v in row.items() if k is not None}
                for row in reader
            )
        return rows, ()
    except (OSError, csv.Error):
        return (), (f"csv_unreadable:{manifest_path}",)


# ---------------------------------------------------------------------------
# The single entry point
# ---------------------------------------------------------------------------


def _merge(*mappings: dict[str, tuple]) -> dict[str, tuple]:
    merged: dict[str, list] = {}
    for mapping in mappings:
        for name, candidates in mapping.items():
            merged.setdefault(name, []).extend(candidates)
    return {name: tuple(candidates) for name, candidates in merged.items()}


def read_submission(output_root: "str | Path", workdir: "Path | None" = None) -> SubmissionValues:
    """Read every published field value reachable from the four real deliverables under
    `output_root`. Never raises - see the module docstring's routing rule and every
    called reader's own never-raise contract.

    `workdir` is where a forced recalculation of `financial_model.xlsx` is performed
    (see `recalc.trusted_values`); a fresh temporary directory is used when omitted.
    """
    root = Path(output_root)
    errors: list[str] = []

    # --- financial_model.xlsx: trusted recalculation first, cached-value fallback second ---
    model_facts: "xlsx_raw.WorkbookFacts | None" = None
    model_trusted = False
    model_path = root / "financial_model.xlsx"
    if model_path.is_file():
        if workdir is not None:
            model_facts, recalc_failure = recalc.trusted_values(model_path, workdir)
        else:
            with tempfile.TemporaryDirectory(prefix="submission_reader_") as tmp:
                model_facts, recalc_failure = recalc.trusted_values(model_path, Path(tmp))
        if model_facts is None:
            errors.append(f"model_recalc_fallback:{recalc_failure}")
            model_facts = xlsx_raw.read_workbook(model_path)  # tolerated, cached-only read
        else:
            model_trusted = True
        errors.extend(f"model_shape_error:{e}" for e in model_facts.shape_errors)
    else:
        errors.append("model_missing")

    model_values = model_named_range_values(model_facts)
    model_facts_for_result = model_facts if model_facts is not None else _empty_workbook_facts()

    # --- decision_memo.pdf: label-anchored text scan ---
    memo_text: "str | None" = None
    memo_path = root / "decision_memo.pdf"
    if memo_path.is_file():
        memo_text, memo_error = cross_file.extract_memo_text(memo_path)
        if memo_error is not None:
            errors.append(memo_error)
    else:
        errors.append("memo_missing")

    memo_values = memo_labelled_values(memo_text)
    entries = gap_list_entries(memo_text)

    # --- assumptions_and_sources.xlsx: header row -> column -> per-row values ---
    assumptions_path = root / "assumptions_and_sources.xlsx"
    assumptions_values: dict[str, tuple] = {}
    if assumptions_path.is_file():
        assumptions_facts = xlsx_raw.read_workbook(assumptions_path)
        errors.extend(f"assumptions_shape_error:{e}" for e in assumptions_facts.shape_errors)
        assumptions_values = assumptions_column_values(assumptions_facts)
    else:
        errors.append("assumptions_missing")

    # --- evidence_manifest.csv: published column order, plus its own rows in order ---
    manifest_path = root / "evidence_manifest.csv"
    manifest_values: dict[str, tuple] = {}
    rows: tuple[dict[str, str], ...] = ()
    if manifest_path.is_file():
        manifest_values, manifest_errors = manifest_column_values(manifest_path)
        errors.extend(manifest_errors)
        rows, row_errors = manifest_rows(manifest_path)
        errors.extend(row_errors)
    else:
        errors.append("manifest_missing")

    merged = _merge(model_values, memo_values, assumptions_values, manifest_values)
    return SubmissionValues(
        values=merged,
        errors=tuple(errors),
        memo_text=memo_text,
        model_facts=model_facts_for_result,
        model_trusted=model_trusted,
        gap_entries=entries,
        manifest_row_records=rows,
    )
