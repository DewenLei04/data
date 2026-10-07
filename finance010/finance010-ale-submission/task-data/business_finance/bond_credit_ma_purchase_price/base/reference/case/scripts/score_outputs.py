"""The deterministic scorer — G0 → G1 → G2, and zero on any gate failure.

The gate structure is the whole point, and it is easy to get subtly wrong. A rubric with 150
items invites a percentage: score = passed / total. That would let a submission that produced
four unparseable files still collect most of the presentation points, and it would let one that
got every canonical figure wrong look respectable because it filled in the fields tidily.

So the score is **gated, not averaged**:

  **G0** every named deliverable is present and parseable. If a file cannot be opened, nothing
        inside it can be assessed, and a score computed from what happened to parse would be
        measuring luck.
  **G1** the business conclusions hold and the figures agree across deliverables. A model whose
        memo and workbook state different numbers has not produced one analysis; it has produced
        two, and at most one of them can be right.
  **G2** every core position passes — the 55 items whose failure means the analysis is *wrong*
        rather than untidily presented.

**Any gate failure scores exactly 0.0.** Not a reduced score: zero. And the passed-position ratio
is carried in `diagnostics` where it is useful for triage, but it **never enters an arithmetic
term that produces the score**. That separation is enforced by a grep in this plan's acceptance
criteria, because the tempting refactor — blending the ratio in "so the number is less harsh" —
would quietly convert a gated rubric back into an average and undo the design.

`evaluate()` in `case/main.py` is the ALE entry point and must never raise on bad output. This
module supports that contract: every reader is fenced, and a file that cannot be parsed becomes a
G0 failure rather than an exception.
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

# Flat imports, matching the convention the sibling scorer modules already use: this package is
# imported with ``case/scripts`` on sys.path, which is also how the acceptance criteria invoke it.
#
# 04-06-PLAN.md Task 1 wires in the three modules that were previously reachable from nothing in
# the scoring path (``alt_methods``, ``recalc``, ``refusal``) alongside ``cross_file``, which was
# already imported but - before this plan - used only for ``CROSS_FILE_TIE``. ``recalc`` has no
# direct call site of its own here: ``submission_reader`` already runs every submitted workbook
# through ``recalc.trusted_values`` before this module ever sees it, and importing it here too is
# what keeps it visible as an attribute of THIS module (the acceptance criterion this plan's own
# checklist names), not a second, redundant recalculation pass.
import alt_methods  # noqa: E402,F401
import cross_file  # noqa: E402
import field_vocabulary as fv  # noqa: E402
import recalc  # noqa: E402,F401
import refusal  # noqa: E402
import rubric_registry as rr  # noqa: E402
import submission_reader as sub_reader  # noqa: E402
import tolerances as tol  # noqa: E402
import xlsx_raw  # noqa: E402

GATE_IDS: tuple[str, ...] = ("G0", "G1", "G2")

# --- 04-05-PLAN.md Task 2: presence-graded BY DESIGN, declared rather than inferred. ---
#
# Every KIND_VALUE item (ANSWER_VALUE, CROSS_FILE_TIE) is, by construction, "value-asserting": its
# whole point is to compare a submitted figure against a canonical one. Before this plan, a
# KIND_VALUE item with no canonical answer to compare against silently fell through to a bare
# presence check - indistinguishable from a field that is presence-graded ON PURPOSE (a structural
# fact, never derived). That fall-through is what let corporate_note_spread_to_treasury_bp,
# muni_tax_equivalent_yield_pct, cecl_expected_credit_loss and locked_box_completion_accounts_delta
# collect full credit for printing ANY number at all - see 04-05-PLAN.md's "The defect".
#
# ANSWER_VALUE items can never legitimately land here: `rubric_registry.build_registry()` fans them
# out directly from `reference/EXPECTED.json`'s own keys (`for key in sorted(expected[half])`), so
# an ANSWER_VALUE item's name is ALWAYS present in `flat_expected` by construction - there is no
# "answer value with no answer" case to allow-list. Only `CROSS_FILE_TIE` items (fanned out from
# `field_vocabulary.CROSS_FILE_TIE_NAMES`, independent of what `EXPECTED.json` happens to contain)
# can lack a canonical entry, and after 04-05-PLAN.md Task 1 exactly two do, each for its own
# individually-justified, documented reason - never a blanket exemption (hard constraint: this set
# must stay small, or the gate is being satisfied by exemption rather than by fixing anything; see
# `tools/check_gradeable_answers.py`'s own over-breadth cap).
PRESENCE_BY_DESIGN: dict[str, str] = {
    "valuation_date": (
        "Genuinely structural: the fixed 'as of' date is STATED in the task prompt/corpus, never "
        "derived by the solver, so there is no 'canonical answer' for it to disagree with - only "
        "whether the memo/model state the one date every other figure is anchored to."
    ),
    "earnout_scenario_table": (
        "Genuinely structural: an array of scenario rows, not a scalar - `reference/EXPECTED.json` "
        "has no single Decimal this shape could be compared against. The scenario-weighted RESULT "
        "(`earnout_expected_value`) is a separate, fully value-graded ANSWER_VALUE/CROSS_FILE_TIE "
        "item; this item is presence-only for the row-level detail feeding that result."
    ),
    # `muni_tax_equivalent_yield_pct` is DELIBERATELY ABSENT from this allow-list as of
    # 04-06-PLAN.md Task 0a, which reverses 04-05-PLAN.md's decision to keep it presence-only. It
    # is now a real `ANSWER_VALUE`/`CROSS_FILE_TIE` key in `reference/EXPECTED.json`'s `bond` half
    # and is graded exactly like any other value-asserting field. The leak-gate collision 04-05
    # found (the correct TEY is byte-identical to a legitimate corpus input under this note's
    # AMT/CAMT combination) is real but does not argue for presence-grading; see
    # `tools/mk_expected.py`'s `_bond_half()` for why value-grading is strictly better, and
    # `tools/probe_leak_check.py`'s `_muni_tey_collapse_exempt_value()` for the narrow, exact-value
    # leak-gate exemption this reversal required instead.
}

# The four artifacts the agent must produce, by their FINAL names. We author .docx sources and the
# user converts them, but the agent-visible contract names only the converted forms (CONV-05).
#
# `ARTIFACT_DELIVERABLES`, never `DELIVERABLES` - the latter ends in the `"shared"` sentinel, and
# requiring a file by that name made G0 unpassable for every submission (ERRATA E3).
REQUIRED_ARTIFACTS: tuple[str, ...] = tuple(
    getattr(spec, "name", spec) for spec in fv.ARTIFACT_DELIVERABLES
)
assert fv.SHARED_SENTINEL not in REQUIRED_ARTIFACTS


@dataclass
class ItemResult:
    check_id: str
    family: str
    gate: str
    starred: bool
    passed: bool
    detail: str = ""
    # The check's target - a rubric id for trap items, a field name otherwise. Carried so a failure can
    # be ATTRIBUTED downstream (tools/triage_probe.py joins it to reference/traps.json via
    # rubric_items_driven). Without it a lost point is just a count, and "we lost 40 points somewhere"
    # cannot tell us whether the case is hard for the reasons it was designed to be hard for.
    target: str = ""


@dataclass
class ScoreReport:
    score: float
    gates: dict[str, bool] = field(default_factory=dict)
    gate_failure: "str | None" = None
    items: list[ItemResult] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)

    def as_mapping(self) -> dict:
        return {
            "score": self.score,
            "gates": dict(self.gates),
            "gate_failure": self.gate_failure,
            "diagnostics": dict(self.diagnostics),
            "item_count": len(self.items),
        }


# ---------------------------------------------------------------------------
# Fenced readers. A submission is untrusted input: it may be absent, empty, truncated, or not the
# format its extension claims. None of that may raise out of this module.
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> "dict | list | None":
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _as_decimal(raw) -> "Decimal | None":
    if raw is None or isinstance(raw, bool):
        return None
    try:
        cleaned = str(raw).replace(",", "").replace("$", "").strip()
    except Exception:  # pragma: no cover - defensive on exotic objects
        return None
    if not cleaned:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


# Used when a value-asserting field has no entry in the published tolerance table. Exact equality
# is the conservative choice: it can only make scoring stricter, never more lenient, so an
# unregistered field cannot quietly widen what counts as correct.
_FALLBACK_TOLERANCE = Decimal("0")


def unregistered_tolerance_fields() -> tuple[str, ...]:
    """Value-asserting rubric fields with no entry in the published tolerance table.

    This is the build-time counterpart to the fallback below. ``tolerances.tolerance_for`` raises
    on an unknown field, which is right for a build check and fatal at score time - ALE's contract
    is that ``evaluate()`` returns a score for bad output and never raises. So scoring catches the
    error and this function makes the same gap visible to a gate, where a loud failure is useful
    instead of destructive.
    """
    missing: list[str] = []
    for check in rr.CHECKS:
        if check.kind != rr.KIND_VALUE:
            continue
        name = check.target.split(".")[-1]
        try:
            tol.tolerance_for(name)
        except KeyError:
            if name not in missing:
                missing.append(name)
    return tuple(missing)


# Accepted spellings for the two boolean answers, so a submission is not marked wrong for writing
# "Yes" where the answer table holds `true`. An analyst writes prose, not JSON literals, and the
# rubric grades the conclusion rather than its encoding.
_TRUE_TOKENS = frozenset({"true", "yes", "y", "triggered", "binding", "crossed", "met", "1"})
_FALSE_TOKENS = frozenset({"false", "no", "n", "not triggered", "not binding", "not crossed",
                           "not met", "0"})


def _as_bool(raw) -> "bool | None":
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return None
    token = str(raw).strip().casefold()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    return None


def values_agree(submitted, expected, field_name: str) -> bool:
    """Compare a submitted value against the canonical one, by the KIND of quantity it is.

    Three comparison modes, because a single numeric path silently fails everything that is not a
    number. Booleans and categorical labels made up four of the canonical answers - whether the CoC
    put triggered, whether the rating gate was crossed, whether the make-whole par floor binds, and
    which synergy regime governs - and `Decimal("true")` is not a number, so every one of them was
    unpassable. They are core answers, so that alone held every score at 0.0 (ERRATA E4).

    Never raises: an unexpected field name or an exotic value must not turn a gradeable-but-wrong
    submission into an infrastructure error.

    `submitted` may be a tuple/list of candidate values rather than one scalar -
    `submission_reader.read_submission` returns every candidate it found for a name across every
    deliverable that could carry it (the "a field published in both is satisfied by either" routing
    rule), never collapsed to a single source ahead of time. Any ONE candidate agreeing is enough;
    an empty tuple (nothing found anywhere) never agrees.
    """
    if isinstance(submitted, (list, tuple)):
        return any(values_agree(candidate, expected, field_name) for candidate in submitted)

    if submitted is None:
        return False

    # Booleans first - `_as_decimal` deliberately rejects bools, so without this branch a correct
    # `true` would be compared as a failed numeric parse.
    want_bool = _as_bool(expected)
    if isinstance(expected, bool) or (want_bool is not None and _as_decimal(expected) is None):
        got_bool = _as_bool(submitted)
        return got_bool is not None and got_bool == want_bool

    left, right = _as_decimal(submitted), _as_decimal(expected)
    if right is None:
        # Categorical label (e.g. the EBITDA regime): case-insensitive exact match. No tolerance
        # concept applies - "capped" and "uncapped" are different answers, not nearby ones.
        return str(submitted).strip().casefold() == str(expected).strip().casefold()
    if left is None:
        return False

    try:
        allowed = tol.tolerance_for(field_name)
    except KeyError:
        allowed = _FALLBACK_TOLERANCE
    return abs(left - right) <= allowed


# ---------------------------------------------------------------------------
# G0 — presence and parseability.
# ---------------------------------------------------------------------------

# Minimal format signatures. Enough to tell "this is not the format the name claims" from "this
# parsed", without dragging a full reader into the gate. Every probe is fenced: a submission is
# untrusted input and no inspection of it may raise.
_ZIP_MAGIC = b"PK\x03\x04"
_PDF_MAGIC = b"%PDF-"


def _parse_failure(path: Path, target: str) -> "str | None":
    """Return a reason string when the file cannot be opened as its name claims, else None."""
    lowered = target.lower()
    # Context-managed: an unclosed handle per probe would accumulate across every deliverable of
    # every scored submission, and on Windows an open handle also blocks deletion of the directory
    # the harness scores in.
    try:
        with path.open("rb") as handle:
            head = handle.read(8)
    except OSError:
        return "unreadable"

    if lowered.endswith(".json"):
        return None if _read_json(path) is not None else "present but not parseable as JSON"
    if lowered.endswith((".xlsx", ".docx", ".pptx")):
        return None if head.startswith(_ZIP_MAGIC) else "present but not a readable office package"
    if lowered.endswith(".pdf"):
        return None if head.startswith(_PDF_MAGIC) else "present but not a readable document"
    if lowered.endswith(".csv"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return "present but not decodable as text"
        return None if text.strip() else "present but empty"
    return None


def evaluate_g0(output_root: Path) -> tuple[bool, list[ItemResult]]:
    results: list[ItemResult] = []
    for check in (c for c in rr.CHECKS if c.gate == "G0"):
        target = check.target
        path = output_root / target
        ok = path.is_file() and path.stat().st_size > 0
        detail = "" if ok else f"{target}: absent or empty"
        # A file that exists but cannot be opened as the format its name claims is a G0 failure -
        # nothing inside it can be assessed - and it must surface as G0 rather than leaking
        # downstream as a pile of missing-field failures at G1, which would misattribute the cause.
        if ok:
            parse_error = _parse_failure(path, target)
            if parse_error is not None:
                ok, detail = False, f"{target}: {parse_error}"
        results.append(
            ItemResult(check.check_id if hasattr(check, "check_id") else check.id,
                       check.family, check.gate, check.starred, ok, detail, target)
        )
    return all(r.passed for r in results), results


# ---------------------------------------------------------------------------
# G1 / G2 — evaluated against the submission, using the frozen answer table.
# ---------------------------------------------------------------------------


def _read_submission(output_root: Path) -> sub_reader.SubmissionValues:
    """The full `submission_reader.SubmissionValues` for `output_root` - `model_facts`,
    `model_trusted` and `memo_text` included, not just the flattened `.values` mapping
    `_submitted_values` returns. `evaluate_items` needs the raw model/memo objects to call
    `cross_file.agrees_across` for `CROSS_FILE_TIE` items (04-05-PLAN.md Task 2); nothing
    else in this module needs more than the flattened dict, hence the split.
    """
    return sub_reader.read_submission(output_root)


def _submitted_values(output_root: Path) -> dict:
    """Flatten whatever the submission provides into a name -> value mapping.

    Deliberately permissive about *where* a value appears: the rubric asserts values, not
    layouts, so a figure reported in the memo or the financial model counts the same. A
    submission is not penalised for putting a correct number in a defensible place.

    Reads the FOUR published deliverables only - decision_memo.pdf, financial_model.xlsx,
    assumptions_and_sources.xlsx, evidence_manifest.csv - via `submission_reader.read_submission`,
    never a JSON sidecar the agent was never asked to produce (see 04-04-PLAN.md). Every value is a
    tuple of candidates (possibly empty); `values_agree`/`_is_supplied` both already treat that
    shape correctly.
    """
    return dict(_read_submission(output_root).values)


def _is_supplied(submitted: dict, name: str) -> bool:
    """Whether the submission actually provided a field.

    Written out rather than inlined as `str(value or "").strip() != ""` because that idiom treats
    every FALSY value as absent: `False` and `0` collapse to `""` and read as "not supplied". Four of
    this case's canonical answers are booleans, and "the par floor does not bind" is a real answer
    that happens to be `False` - marking it missing penalises a correct negative conclusion, which is
    the opposite of what the item is for.
    """
    if name not in submitted:
        return False
    value = submitted[name]
    if value is None:
        return False
    if isinstance(value, bool):
        return True  # both True and False are answers
    if isinstance(value, (int, float, Decimal)):
        return True  # 0 is an answer
    if isinstance(value, (list, dict, tuple)):
        return len(value) > 0
    return str(value).strip() != ""


def _trap_index() -> dict[str, dict]:
    """rubric id -> {graded_field, wrong}, from the private trap manifest."""
    repo = Path(__file__).resolve().parents[2]
    rows = _read_json(repo / "reference" / "traps.json")
    entries = rows.get("traps") if isinstance(rows, dict) else rows
    index: dict[str, dict] = {}
    for row in entries or ():
        if not isinstance(row, dict):
            continue
        wrong = (row.get("cost_if_wrong") or {}).get("wrong")
        for rubric_id in row.get("rubric_items_driven") or ():
            index[rubric_id] = {
                "graded_field": row.get("graded_field") or "",
                "wrong": wrong,
                "trap_id": row.get("id"),
            }
    return index


_EMPTY_WORKBOOK_FACTS = xlsx_raw.WorkbookFacts(
    path=Path(), sheets=(), cells={}, defined_names={}, shared_strings=()
)

# `field_vocabulary.CROSS_FILE_TIE_NAMES` is fanned out over the intersection of
# DECISION_MEMO_FIELDS and FINANCIAL_MODEL_NAMED_RANGES ONLY - never
# ASSUMPTIONS_FIELDS or EVIDENCE_MANIFEST_COLUMNS, which name a completely different
# vocabulary. So "every deliverable that publishes that field" (04-05-PLAN.md Task 2)
# is always exactly these two for this family - see `cross_file.agrees_across`'s own
# docstring for why requiring all four would make every item in this family unpassable.
_CROSS_FILE_TIE_REQUIRED: frozenset[str] = frozenset({"financial_model.xlsx", "decision_memo.pdf"})


def _evaluate_cross_file_tie(
    name: str, want_raw: object, submission: "sub_reader.SubmissionValues", submitted: dict
) -> tuple[bool, str]:
    """A `CROSS_FILE_TIE` item actually ties: the canonical value must appear in BOTH
    `financial_model.xlsx` and `decision_memo.pdf`, via `cross_file.agrees_across` -
    never the presence-only fall-through 04-05-PLAN.md Task 2 exists to remove.

    Boolean/categorical `CROSS_FILE_TIE` fields (e.g. `rating_gate_crossed`) fall back to
    the existing tuple-of-candidates comparison via `values_agree`: `agrees_across` is
    Decimal-only by construction (it scans each deliverable's text/cells for a NUMBER),
    and a boolean conclusion is never printed as a bare number.
    """
    want = _as_decimal(want_raw)
    if want is None:
        ok = values_agree(submitted.get(name), want_raw, name)
        return ok, ("" if ok else f"{name}: value absent or outside tolerance")

    # `trusted=True` always, deliberately, NOT `submission.model_trusted`. `agrees_across`'s
    # `trusted` flag exists to stop a raw, never-recalculated `.xlsx` cache from being read as if
    # it were verified computed output (an adversarial-submission concern). But
    # `submission.model_facts` is already the SAME best-available read every ANSWER_VALUE/
    # RANGE_CALCULATED item in this scorer accepts without further gating: `submission_reader`
    # attempts a forced `recalc.trusted_values` pass FIRST and only falls back to the file's own
    # cached values when the recalculation TOOLCHAIN itself is unavailable on this host (no
    # `soffice`) - an infrastructure limitation, never a submission trying to fake a value. Passing
    # `submission.model_trusted` here would make CROSS_FILE_TIE items fail on any scoring host
    # without LibreOffice installed, while the identical figure's ANSWER_VALUE item (read via the
    # same `model_facts` through a named range) passes fine - an environment-dependent
    # inconsistency found while wiring this in (04-05-PLAN.md Task 2), against
    # `tools/submission_fixture.py`'s own positive fixture on a host with no `soffice` on PATH.
    result = cross_file.agrees_across(
        name,
        want,
        model_facts=submission.model_facts,
        assumptions_facts=_EMPTY_WORKBOOK_FACTS,
        manifest_values=(),
        memo_text=submission.memo_text,
        trusted=True,
        required=_CROSS_FILE_TIE_REQUIRED,
    )
    if result.passed:
        return True, ""
    return False, f"{name}: missing_in={','.join(result.missing_in) or 'unknown'}"


# ---------------------------------------------------------------------------
# 04-06-PLAN.md Task 1 - one enforcing branch per remaining declared kind. Task 0 pins the
# semantics not already fixed by rubric_registry's own module docstring (VALUE/CALCULATED/
# EXCLUSIVE/TRACEABLE are pinned there):
#   REFERENCED - financial_model.schema.json distinguishes `must_be_calculated` from
#                `must_reference_range` ("the name must actually resolve to a workbook reference
#                rather than merely exist"). `xlsx_raw.named_range_resolves` implements EXACTLY
#                that - never whether some OTHER cell's formula also cites the name by
#                identifier, which is a stricter, unpublished rule 04-09-PLAN.md removed (it was
#                failing 12/26 of probe2's real named ranges for a convention the agent was
#                never told to follow).
#   SHAPE      - provenance_envelope.schema.json / refusal_gap_list.schema.json state the
#                required shape directly - see _evaluate_provenance_shape / _evaluate_gap_shape.
#   POLICY     - alt_methods.METHOD_POLICY (the governing keyword _evaluate_policy checks against,
#                below). alt_methods.requires_method_naming is deliberately NOT part of this
#                enforcement path - it answers a different question ("does this field have more
#                than one defensible method"), never "is naming optional here" (04-08-PLAN.md
#                Task 2; see that function's own docstring for why calling it here would be wrong).
# ---------------------------------------------------------------------------


def _evaluate_referenced(name: str, model_facts: "xlsx_raw.WorkbookFacts") -> tuple[bool, str]:
    """REFERENCED (RANGE_USED). Existence of the declared name is not enough
    (`financial_model.schema.json`'s `must_reference_range`): it must resolve to a real anchor
    cell on an actually-declared sheet - a dangling name, an `#REF!`, or a literal-constant
    defined name still fails. It is NOT required that some OTHER cell's formula cite the name by
    identifier - that was a stricter, unpublished rule this item used to enforce; see
    04-09-PLAN.md. `xlsx_raw.named_range_resolves` already implements exactly this; nothing here
    re-derives it."""
    ok = xlsx_raw.named_range_resolves(model_facts, name)
    detail = "" if ok else f"{name}: named range not declared, or does not resolve to a real workbook reference"
    return ok, detail


def _evaluate_calculated(name: str, model_facts: "xlsx_raw.WorkbookFacts") -> tuple[bool, str]:
    """CALCULATED (RANGE_CALCULATED). "Fails a hard-coded cell even when its value is right,
    because the workbook was required to compute it" (registry docstring) - so this reads the
    named range's anchor cell's FORMULA TEXT (an `<f>` element's presence), never its cached `<v>`,
    which is the exact distinction `xlsx_raw.named_range_is_calculated` is built to make: a
    hard-coded cell and a formula cell whose cached value survived recalculation are
    indistinguishable if only the value is read (Task 1's own warning)."""
    ok = xlsx_raw.named_range_is_calculated(model_facts, name)
    detail = "" if ok else f"{name}: named range resolves to a hard-coded cell, not a live formula"
    return ok, detail


def _evaluate_exclusive(
    name: str, submission: "sub_reader.SubmissionValues", submitted: dict
) -> tuple[bool, str]:
    """EXCLUSIVE (MEMO_FIELD). "Fails a submission that states two contradictory answers for the
    same field, so hedging both ways cannot win" (registry docstring) - a CONTRADICTION test, not
    a presence test.

    Deliberately NARROW (hard constraint: a memo that legitimately reports a rejected
    alternative - e.g. `locked_box_completion_accounts_delta` exists precisely to compare the
    superseded locked-box precedent against the governing completion-accounts mechanism - must
    not fail this). Only the field's OWN `x-memo-label`, printed more than once with a
    disagreeing value at each occurrence, counts as hedging. Showing your work under OTHER labels
    (a separate "locked-box price" line, a separate "completion-accounts price" line) never
    repeats THIS field's label, so it never reaches this check at all.

    What this cannot catch (recorded per hard constraint 7 rather than guessed at): hedging
    phrased without repeating the field's own label - e.g. "either $X or $Y depending on
    interpretation" stated once, in one breath, under one occurrence of the label. Catching that
    would require parsing prose for disjunction, which this scorer's label-anchored extraction
    was never built to do.

    ARRAY-kind fields (`gap_list`, `earnout_scenario_table`) are exempt from the occurrence scan
    and fall back to presence: "two contradictory values" is a scalar concept, and an array
    field's own label is also a plausible PREFIX of an unrelated heading a memo legitimately uses
    right below it (this fixture's own "Gap List Detail" heading contains "Gap List" as a
    substring, which a naive occurrence scan mistook for a second, disagreeing occurrence of the
    `gap_list` field itself - found and fixed while building this check's own positive-fixture
    proof, not a hypothetical).
    """
    label = sub_reader.MEMO_LABELS.get(name)
    if not label or submission.memo_text is None:
        ok = _is_supplied(submitted, name)
        return ok, "" if ok else f"{name}: not supplied"

    if sub_reader._FIELD_KIND.get(name) == "array":  # noqa: SLF001 - see docstring above
        ok = _is_supplied(submitted, name)
        return ok, "" if ok else f"{name}: not supplied"

    occurrences = sub_reader.find_all_label_values(submission.memo_text, label)
    if not occurrences:
        ok = _is_supplied(submitted, name)
        return ok, "" if ok else f"{name}: not supplied"

    kind = sub_reader._FIELD_KIND.get(name)  # noqa: SLF001 - same module family, not a layering break
    distinct: list = []
    if kind == "number":
        for occurrence in occurrences:
            candidates = cross_file.numeric_candidates(occurrence)
            if not candidates:
                continue
            value = candidates[0]
            try:
                allowed = tol.tolerance_for(name)
            except KeyError:
                allowed = _FALLBACK_TOLERANCE
            if not any(tol.eq(value, seen, allowed) for seen in distinct):
                distinct.append(value)
    else:
        for occurrence in occurrences:
            folded = occurrence.strip().casefold()
            if folded and folded not in distinct:
                distinct.append(folded)

    if not distinct:
        return False, f"{name}: not supplied"
    if len(distinct) > 1:
        return False, f"{name}: memo states {len(distinct)} contradictory values under its own label"
    return True, ""


# TRACEABLE's citation field, per family - "requires a citation, so a paraphrase cannot satisfy
# it" (registry docstring). `basis` is ASSUMPTIONS_FIELDS' own citation column;
# `structural_identifier` is the PROVENANCE_FIELDS leaf `EVIDENCE_MANIFEST_COLUMN_ORDER` flattens
# onto every evidence-manifest row - "the locator the scorer trusts" (field_vocabulary.py).
_TRACEABLE_CITATION_FIELD: dict[str, str] = {
    "ASSUMPTION_FIELD": "basis",
    "EVIDENCE_COLUMN": "structural_identifier",
}


def _evaluate_traceable(name: str, family: str, submitted: dict) -> tuple[bool, str]:
    """TRACEABLE (ASSUMPTION_FIELD / EVIDENCE_COLUMN). The field's own value being present is not
    enough; its row's citation field must ALSO be present, never a paraphrase standing alone.

    This is citation PRESENCE, not citation correctness: `submission_reader` currently resolves
    each named column to its FIRST non-empty value across the whole sheet/CSV, with no per-row
    linkage back to the specific field this item targets - so this check cannot verify the cited
    document/section is actually the right one for THIS field, only that some real locator was
    supplied rather than the field being asserted bare. Recorded here rather than silently
    presented as a stronger check than it is.
    """
    if not _is_supplied(submitted, name):
        return False, f"{name}: not supplied"
    citation_field = _TRACEABLE_CITATION_FIELD[family]
    if not _is_supplied(submitted, citation_field):
        return False, f"{name}: supplied with no citation ({citation_field} absent)"
    return True, ""


_PROVENANCE_REQUIRED_LEAVES: tuple[str, ...] = (
    "source_document_id",
    "structural_identifier",
    "document_effective_date",
    "retrieved_date",
    "source_sha256",
)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# 04-07-PLAN.md Task 3 - TRACEABLE correctness's actual enforcing branch (PROVENANCE_FIELD's
# own `source_document_id` leaf, below): "check only that source_document_id names a real corpus
# document" (hard constraint 5) - never whether the cited SECTION actually supports the claim,
# which is a judge-model problem explicitly deferred to v2 and NOT implemented here, even as a
# weak proxy.
_INPUT_ROOT = Path(__file__).resolve().parents[2] / "input"


@functools.lru_cache(maxsize=1)
def _corpus_document_stems() -> frozenset[str]:
    """Every real corpus document's own bare file STEM (no path, no extension),
    casefolded - the convention `evidence_manifest.csv`'s own fixture already uses (e.g.
    `spa_locked_box_precedent`). Mechanically derived from the actual `input/` tree on
    disk every time (cached for the process's lifetime only - never a hand-curated
    registry restating the corpus a second time, and never stale across a rebuild that
    adds or renames a document).

    Stem-only, deliberately: a real agent may cite the SAME real document under a
    different extension than the one actually staged (e.g. `credit_agreement.pdf` after
    printing the corpus's own `.docx` to PDF for reading) - `_corpus_document_ids_match`
    strips the candidate's own extension the same way before comparing, so this set only
    needs to hold one canonical spelling per document, never every plausible extension.

    An absent `input/` directory (a stripped-down checkout) yields an EMPTY set, which
    fails every `source_document_id` check rather than silently passing one - loud, not
    silent, per this module's own never-fabricate-a-pass discipline.
    """
    stems: set[str] = set()
    if _INPUT_ROOT.is_dir():
        for path in _INPUT_ROOT.rglob("*"):
            if path.is_file():
                stems.add(path.stem.casefold())
    return frozenset(stems)


def _names_a_corpus_document(candidate: str) -> bool:
    """True iff `candidate` (a submitted `source_document_id`, with or without a path
    prefix or a file extension - possibly the WRONG extension, per
    `_corpus_document_stems`'s own docstring) resolves, by STEM alone, to a real corpus
    document. `Path(candidate).stem` already handles a path-prefixed candidate (it
    operates on the final path component) and a bare-name candidate identically, so no
    separate branch is needed for either shape."""
    if not candidate:
        return False
    return Path(candidate).stem.casefold() in _corpus_document_stems()


def _candidates_of(value: object) -> tuple:
    return value if isinstance(value, (list, tuple)) else (value,)


def _evaluate_provenance_shape(name: str, submitted: dict) -> tuple[bool, str]:
    """SHAPE (PROVENANCE_FIELD). `provenance_envelope.schema.json`'s own required set: five
    leaves required (`page_hint` is the one optional leaf). The `provenance` sentinel item is the
    WHOLE envelope's own shape check - ALL FIVE required leaves present, never merely "any one of
    six" (submission_reader's COMPOSITE route is a presence convenience for extraction, not this
    item's actual contract). Each individual leaf item asserts its own presence plus, where the
    schema pins a `pattern`, that the value actually MATCHES it - a malformed date or a non-hex
    digest string is not "shaped" even though something was printed.
    """
    if name == "provenance":
        missing = [leaf for leaf in _PROVENANCE_REQUIRED_LEAVES if not _is_supplied(submitted, leaf)]
        if missing:
            return False, f"provenance: required leaf(s) missing: {', '.join(missing)}"
        return True, ""

    if not _is_supplied(submitted, name):
        return False, f"{name}: not supplied"

    texts = tuple(str(c).strip() for c in _candidates_of(submitted.get(name)) if c is not None)
    if name in ("document_effective_date", "retrieved_date"):
        ok = any(_ISO_DATE_RE.match(t) for t in texts)
        return ok, "" if ok else f"{name}: not a valid ISO-8601 (YYYY-MM-DD) date"
    if name == "source_sha256":
        ok = any(_SHA256_HEX_RE.match(t) for t in texts)
        return ok, "" if ok else f"{name}: not a 64-character lowercase hex digest"
    if name == "source_document_id":
        # 04-07-PLAN.md Task 3: presence used to be sufficient here, so
        # `source_document_id: "the indenture"` passed. Check the id against the actual
        # corpus - never whether the cited SECTION supports the claim (hard constraint
        # 5, deferred to a v2 judge model, not approximated here).
        ok = any(_names_a_corpus_document(t) for t in texts)
        detail = "" if ok else f"{name}: does not name a real corpus document under input/"
        return ok, detail
    return True, ""  # structural_identifier / page_hint: presence is the shape


_GAP_TYPE_ENUM: frozenset[str] = frozenset(
    {
        "missing_source",
        "missing_authorization",
        "missing_timestamp",
        "conflicting_authorization",
        "post_valuation_date_information",
        "illegible_scan",
        "internally_inconsistent_source",
    }
)
_CLAIM_ID_RE = re.compile(r"^CLM-\d{3,4}$")


def _answerable_field_names(flat_expected: dict) -> frozenset[str]:
    """Every published field name this corpus can grade a definitive value for, derived at
    score time - never hand-listed (04-08-PLAN.md hard constraint 2).

    `flat_expected`'s own keys are exactly the fields `evaluate_items` already resolves a
    canonical value for (bridged to published names via `ANSWER_KEY_TO_PUBLISHED_FIELD`, the
    identical construction the ANSWER_VALUE/CROSS_FILE_TIE branch above uses) - so this is not a
    second, potentially-divergent derivation of "answerable"; it is the SAME set, re-used. A
    future `reference/EXPECTED.json` addition (04-05 added five) changes both what gets GRADED
    and what counts as a false refusal in the same commit, with nothing here to update by hand.

    UNION `PRESENCE_BY_DESIGN`, deliberately, not left out (04-08-PLAN.md hard constraint 3 -
    this must be an explicit choice, not an accidental fall-through). Every `PRESENCE_BY_DESIGN`
    field has no single canonical Decimal to diff against - that is exactly why it is
    presence-graded rather than value-graded - but each one's own recorded rationale says the
    information is a STATED corpus fact, not a genuine unknown: `valuation_date` "is STATED in
    the task prompt/corpus, never derived by the solver"; `earnout_scenario_table` is "row-level
    detail feeding" a fully value-graded result. A gap entry naming either as its blocked_field
    is not reporting a real evidentiary gap - the information was never withheld from the agent
    in the first place - so both fall on the "false refusal" side of the line, same as any
    ANSWER_VALUE/CROSS_FILE_TIE field.
    """
    return frozenset(flat_expected) | frozenset(PRESENCE_BY_DESIGN)


def _evaluate_gap_shape(
    name: str,
    submission: "sub_reader.SubmissionValues",
    submitted: dict,
    answerable_fields: frozenset[str],
) -> tuple[bool, str]:
    """SHAPE (GAP_SHAPE). `refusal_gap_list.schema.json`'s own explicit rule: "every entry is
    graded against its own claim_id, paired against evidence_manifest.csv's claim_id for that
    same claim" - plus its per-field structural constraints (`gap_type`'s enum, `blocked_field`
    naming a real published field AND naming a field this corpus cannot answer,
    `what_would_resolve` never bare boilerplate, `definitive_conclusion_withheld` a real
    boolean). `refusal.BOILERPLATE_REASONS` is reused verbatim for the last of these.

    `claim_id` (04-07-PLAN.md Task 3) now enforces BOTH halves the schema promises: the
    PATTERN (`^CLM-[0-9]{3,4}$`, unchanged from 04-06) AND the pairing - every gap-list
    entry actually printed in the memo (`submission.gap_entries`, 04-07-PLAN.md Task 2's
    row-preserving reader) must carry a `claim_id` that also appears as SOME
    `evidence_manifest.csv` row's own `claim_id` (`submission.manifest_row_records`). An
    entry bound to nothing evidenced is not a claim-bound refusal; it is a disclaimer -
    exactly the failure mode the schema's own description warns against ("a gap entry
    that names no specific claim ... scores as a failure to answer", TASK_PROMPT.md).

    `blocked_field` (04-08-PLAN.md Task 1) is graded the SAME way, on the SAME per-entry
    route, for the paired "no false refusal" half of the schema's promise: refusing an
    answerable claim must lose credit, not merely fail to earn its own ANSWER_VALUE item.
    Before this plan, `blocked_field` was checked for vocabulary membership only - "is this
    a real published field name" - so a gap entry could name ANY field, including one this
    corpus already answers, and still collect full GAP_SHAPE credit unconditionally. That
    made refusal a free hedge: it never earned the ANSWER_VALUE item (as before), but it
    also never lost anything a wrong commitment would have lost, and it additionally
    collected this item's own credit for free. `blocked_field` is the key this check uses,
    never `claim_id` - see this plan's own key_decisions for why `claim_id` (agent-invented,
    unmatchable to any corpus-side registry) cannot carry it and `blocked_field` (drawn from
    our controlled, corpus-shared vocabulary) can.
    """
    if not _is_supplied(submitted, name):
        return False, f"{name}: not supplied"

    texts = tuple(str(c).strip() for c in _candidates_of(submitted.get(name)) if c is not None and str(c).strip())

    if name == "claim_id":
        ok = any(_CLAIM_ID_RE.match(t) for t in texts)
        if not ok:
            return False, f"{name}: does not match the published CLM-### pattern"

        entries = submission.gap_entries
        if not entries:
            return False, f"{name}: gap_list has no per-entry claim_id extractable from the memo"

        manifest_claim_ids = frozenset(
            row["claim_id"] for row in submission.manifest_row_records if row.get("claim_id")
        )
        unpaired = tuple(
            entry["claim_id"]
            for entry in entries
            if entry.get("claim_id") and entry["claim_id"] not in manifest_claim_ids
        )
        if unpaired:
            return False, (
                f"{name}: gap entry claim_id(s) {unpaired} have no matching "
                "evidence_manifest.csv row - a claim-bound refusal must be evidenced, "
                "never bound to nothing"
            )
        return True, ""

    if name == "gap_type":
        ok = any(t in _GAP_TYPE_ENUM for t in texts)
        return ok, "" if ok else f"{name}: not one of the published gap-type labels"

    if name == "blocked_field":
        ok = any(t in fv.ALL_PUBLISHED_NAMES for t in texts)
        if not ok:
            return False, f"{name}: does not name a published output field"

        # No-false-refusal half: every gap entry ACTUALLY PRINTED (not the flat
        # first-value route `texts` above - a submission can print several gap entries,
        # and each one is its own promise) must name a field this corpus has no
        # canonical answer for. Naming one it does have an answer for is a false
        # refusal - it fails this item even though the field's own vocabulary spelling
        # was real and even though its ANSWER_VALUE/CROSS_FILE_TIE item already failed
        # separately for never being supplied. Honest refusal (naming a genuinely
        # unanswerable field) keeps this item exactly as before - this branch never
        # fails a gap entry for THAT reason.
        false_refusals = tuple(
            entry["blocked_field"]
            for entry in submission.gap_entries
            if entry.get("blocked_field") and entry["blocked_field"] in answerable_fields
        )
        if false_refusals:
            return False, (
                f"{name}: gap entry blocked_field {false_refusals} names a field this "
                "corpus can answer - refusing an answerable claim loses its credit, "
                "not a free hedge"
            )
        return True, ""

    if name == "what_would_resolve":
        ok = any(t.casefold() not in refusal.BOILERPLATE_REASONS for t in texts)
        detail = "" if ok else f"{name}: generic boilerplate reason, names no specific missing evidence"
        return ok, detail

    if name == "definitive_conclusion_withheld":
        ok = any(_as_bool(c) is not None for c in _candidates_of(submitted.get(name)))
        return ok, "" if ok else f"{name}: not a recognisable boolean"

    if name == "evidence_checked":
        return True, ""  # presence only - see this function's own docstring's extraction note

    raise AssertionError(f"GAP_SHAPE has no enforcing branch for field {name!r}")


# POLICY's naming field -> the METHOD_POLICY-table key it governs. The rubric's two METHOD_POLICY
# items target the memo's METHOD-NAMING fields (earnout_valuation_method,
# make_whole_discount_target); alt_methods.METHOD_POLICY is keyed by the FIGURE those method
# choices produce (earnout_expected_value, make_whole_amount) - the same
# calculation-name-vs-published-field-name split field_vocabulary.ANSWER_KEY_TO_PUBLISHED_FIELD
# already bridges for VALUE items, restated here for POLICY's own two items.
_METHOD_POLICY_BRIDGE: dict[str, str] = {
    "earnout_valuation_method": "earnout_expected_value",
    "make_whole_discount_target": "make_whole_amount",
}

# The GOVERNING method-name keyword for each PINNED metric this family covers today, derived
# directly from alt_methods.METHOD_POLICY's own `rationale` text (never guessed): "a single
# probability-weighted scenario tree" for the earn-out; "discounts to the par call date" for the
# make-whole. `alt_methods.py` has zero "alternatives" entries in this build (its own module
# docstring) - see _evaluate_policy's NotImplementedError branch for what happens if that changes.
_METHOD_POLICY_GOVERNING_KEYWORD: dict[str, str] = {
    "earnout_expected_value": "probability-weighted",
    "make_whole_amount": "par call",
}


def _evaluate_policy(name: str, submitted: dict) -> tuple[bool, str]:
    """POLICY (METHOD_POLICY). "The governing method was applied and named" (rubric_registry
    module docstring) - presence of SOME name is not enough; the NAMED method must be the one
    `alt_methods.METHOD_POLICY` actually governs by."""
    bridged = _METHOD_POLICY_BRIDGE[name]
    policy = alt_methods.METHOD_POLICY[bridged]

    texts = tuple(str(c).strip() for c in _candidates_of(submitted.get(name)) if c is not None and str(c).strip())
    if not texts:
        return False, f"{name}: not supplied"

    if policy.mode == "alternatives":
        # Not reachable today (alt_methods.py's own docstring: zero "alternatives" entries exist
        # in this build) - fails LOUDLY rather than silently accepting any name, per hard
        # constraint 8: a kind graded on a guessed keyword table is worse than one that stops.
        raise NotImplementedError(
            f"alt_methods.METHOD_POLICY[{bridged!r}] switched to 'alternatives' mode, which "
            "_METHOD_POLICY_GOVERNING_KEYWORD has no table for - extend this function before "
            "relying on it for a newly-unpinned policy."
        )

    keyword = _METHOD_POLICY_GOVERNING_KEYWORD[bridged]
    ok = any(keyword in t.casefold() for t in texts)
    detail = "" if ok else f"{name}: does not name the governing method ({policy.rationale})"
    return ok, detail


def evaluate_items(output_root: Path, expected: dict, gate: str) -> list[ItemResult]:
    submission = _read_submission(output_root)
    submitted = dict(submission.values)

    # Canonical values keyed by the name the AGENT publishes, not the name the solver used. The
    # answer table calls a figure `make_whole_amount_due`; the deliverable field is
    # `make_whole_amount`. Grading the submission under the solver's spelling looked for a key no
    # agent was ever asked for, so the item could not be passed (ERRATA E4).
    bridge = dict(getattr(fv, "ANSWER_KEY_TO_PUBLISHED_FIELD", {}) or {})
    flat_expected: dict = {}
    for half in ("bond", "spa", "coupled"):
        for key, value in (expected.get(half) or {}).items():
            flat_expected.setdefault(bridge.get(key, key), value)
    answerable_fields = _answerable_field_names(flat_expected)

    traps = _trap_index()
    results: list[ItemResult] = []
    for check in (c for c in rr.CHECKS if c.gate == gate):
        item_id = getattr(check, "id", None) or getattr(check, "check_id", "?")
        target = check.target
        name = bridge.get(target.split(".")[-1], target.split(".")[-1])

        if check.family == "TRAP_DRIVEN":
            ok, detail = _evaluate_trap_item(target, traps, submitted, flat_expected)
        elif check.kind == rr.KIND_VALUE:
            # 04-05-PLAN.md Task 2: the fall-through no longer absorbs two different
            # situations. A value-asserting item either (a) has a canonical answer -
            # graded against it, (b) is declared presence-graded BY DESIGN in
            # PRESENCE_BY_DESIGN, with its own one-line reason - or (c) has no canonical
            # answer and is NOT in that allow-list, which is now a FAIL naming the
            # missing answer, never a silent pass on presence.
            if name in flat_expected:
                if check.family == "CROSS_FILE_TIE":
                    ok, detail = _evaluate_cross_file_tie(name, flat_expected[name], submission, submitted)
                else:
                    # ANSWER_VALUE: always resolvable here by construction (fanned out
                    # directly from `expected[half]`'s own keys), never the fall-through.
                    ok = values_agree(submitted.get(name), flat_expected[name], name)
                    detail = "" if ok else f"{name}: value absent or outside tolerance"
            elif name in PRESENCE_BY_DESIGN:
                ok = _is_supplied(submitted, name)
                detail = "" if ok else f"{name}: not supplied"
            else:
                ok = False
                detail = (
                    f"{name}: value-asserting item has no canonical answer to grade against "
                    "(not in PRESENCE_BY_DESIGN either - see tools/check_gradeable_answers.py)"
                )
        elif check.kind == rr.KIND_CALCULATED:
            ok, detail = _evaluate_calculated(name, submission.model_facts)
        elif check.kind == rr.KIND_REFERENCED:
            ok, detail = _evaluate_referenced(name, submission.model_facts)
        elif check.kind == rr.KIND_EXCLUSIVE:
            ok, detail = _evaluate_exclusive(name, submission, submitted)
        elif check.kind == rr.KIND_TRACEABLE:
            ok, detail = _evaluate_traceable(name, check.family, submitted)
        elif check.kind == rr.KIND_SHAPE:
            if check.family == "GAP_SHAPE":
                ok, detail = _evaluate_gap_shape(name, submission, submitted, answerable_fields)
            elif check.family == "PROVENANCE_FIELD":
                ok, detail = _evaluate_provenance_shape(name, submitted)
            else:
                # No catch-all fall-through (hard constraint 2): an unrecognised SHAPE family
                # must fail loudly at score time rather than silently pass on presence.
                raise AssertionError(
                    f"KIND_SHAPE family {check.family!r} has no enforcing branch in evaluate_items"
                )
        elif check.kind == rr.KIND_POLICY:
            ok, detail = _evaluate_policy(name, submitted)
        else:
            # No catch-all fall-through (hard constraint 2): every KIND_* constant declared in
            # rubric_registry.py must have an explicit branch above. An unrecognised kind fails
            # loudly at score time - tools/check_kind_enforced.py proves this by execution.
            raise AssertionError(
                f"{check.kind!r} has no enforcing branch in evaluate_items - see "
                "rubric_registry.py's KIND_* constants and tools/check_kind_enforced.py"
            )
        results.append(
            ItemResult(item_id, check.family, check.gate, check.starred, ok, detail, target)
        )
    return results


def _evaluate_trap_item(rubric_id: str, traps: dict, submitted: dict,
                        flat_expected: dict) -> tuple[bool, str]:
    """A trap item asserts two things about the field the trap corrupts.

    The submission must (a) match the canonical value and (b) NOT match the trap's own documented
    wrong value. Both halves matter. Without (a) the item would pass any answer that merely differs
    from the trap value, rewarding a submission for being wrong in a new way. Without (b) the item is
    just a duplicate of the answer-value item and grades the same insight twice.
    """
    binding = traps.get(rubric_id)
    if not binding or not binding.get("graded_field"):
        # Unbound trap: report it rather than passing it. A silently-passed core item would hide the
        # very defect check_derivable exists to prevent.
        return False, f"{rubric_id}: trap declares no graded_field (see tools/traps.py)"

    field = binding["graded_field"]
    value = submitted.get(field)

    # Flagging traps (the correct behaviour is a gap-list entry, not a number) have no canonical
    # value to match; the assertion is that the gap list is present and non-empty. Reuses
    # `_is_supplied` rather than re-deriving "empty" by hand - `value` here is a tuple of candidates
    # (see `submission_reader.SubmissionValues`), and an empty tuple is not literally `[]` or `{}`,
    # so the old inline check silently treated "found nothing" as "supplied".
    if field not in flat_expected:
        supplied = _is_supplied(submitted, field)
        return supplied, "" if supplied else f"{rubric_id}: {field} not supplied or empty"

    if not values_agree(value, flat_expected[field], field):
        return False, f"{rubric_id}: {field} does not match the canonical value"
    if binding.get("wrong") is not None and values_agree(value, binding["wrong"], field):
        return False, f"{rubric_id}: {field} matches the trap's documented wrong value"
    return True, ""


# ---------------------------------------------------------------------------
# The gated score.
# ---------------------------------------------------------------------------


def score(output_root: "str | Path", expected_path: "str | Path | None" = None) -> ScoreReport:
    """Score a submission. Returns 0.0 on any gate failure; never raises on bad output."""
    root = Path(output_root)
    repo = Path(__file__).resolve().parents[2]
    expected = _read_json(Path(expected_path) if expected_path else repo / "reference" / "EXPECTED.json") or {}

    report = ScoreReport(score=0.0)

    g0_ok, g0_items = evaluate_g0(root)
    report.gates["G0"] = g0_ok
    report.items.extend(g0_items)

    g1_items = evaluate_items(root, expected, "G1")
    g2_items = evaluate_items(root, expected, "G2")
    report.items.extend(g1_items)
    report.items.extend(g2_items)

    g1_ok = all(r.passed for r in g1_items)
    g2_ok = all(r.passed for r in g2_items if r.starred)
    report.gates["G1"] = g1_ok
    report.gates["G2"] = g2_ok

    passed = sum(1 for r in report.items if r.passed)
    total = len(report.items)
    starred_passed = sum(1 for r in report.items if r.starred and r.passed)
    starred_total = sum(1 for r in report.items if r.starred)

    # Diagnostics only. These are for triage - deciding whether a low score reflects a hard case
    # or a broken run - and are deliberately kept out of the score computation below.
    report.diagnostics = {
        "passed_ratio": (passed / total) if total else 0.0,
        "passed_positions": passed,
        "total_positions": total,
        "starred_passed": starred_passed,
        "starred_total": starred_total,
        "failures": [r.detail for r in report.items if not r.passed and r.detail][:20],
        # Every lost position's target, not a truncated sample: attribution downstream needs the full
        # set to say whether losses trace to the coupled chain, to cross-file comparison, or to
        # formatting - and a truncated list would silently bias that answer.
        "lost_items": [r.target for r in report.items if not r.passed and r.target],
    }

    for gate_id in GATE_IDS:
        if not report.gates.get(gate_id, False):
            report.gate_failure = gate_id
            report.score = 0.0
            return report

    # Every gate held. The score is 1.0 - a gated rubric answers "did this submission meet the
    # standard", not "how close did it get". Partial credit lives in diagnostics.
    report.score = 1.0
    return report


def score_value(output_root: "str | Path") -> float:
    """Thin wrapper returning just the number, for ALE's ``evaluate()`` hook."""
    return score(output_root).score
