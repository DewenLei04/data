"""case/scripts/cross_file.py - cross-file numeric agreement across all four deliverables
(SCORE-05, RESEARCH Q5(a)).

Implements finance001's `workbook_carries(field, tol)` pattern (ARCHITECTURE.md Sec4),
extended to all four deliverables named in BRIEF Sec4: `financial_model.xlsx`,
`assumptions_and_sources.xlsx`, `evidence_manifest.csv`, `decision_memo.pdf`. Deliberately
**loose-location, strict-value**: a governing figure may live anywhere in the memo's
prose, but it must parse as the exact correct number within tolerance, not merely appear
"nearby" in context.

Every comparison in this module runs through `tolerances.eq()`/`tolerances.number()` -
Decimal only, never `float`, enforced by a grep gate in `02-04-PLAN.md`'s acceptance
criteria.
"""

import csv
import re
import shutil
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import tolerances
import xlsx_raw

_PDFTOTEXT_TIMEOUT_S = 30

# Optional third-party fallback text extractor - never a hard dependency. Guarded by
# try/except ImportError at CALL time (not at module import time) so this module never
# fails to import on a host where it is absent.

_CANDIDATE_RE = re.compile(r"\(?\$?-?\d[\d,]*(?:\.\d+)?%?\)?")


def numeric_candidates(text: str) -> tuple[Decimal, ...]:
    """Extract every numeric-looking token from free text (currency prefixes, thousands
    separators, decimals, accounting parentheses) and normalise each through
    `tolerances.number`, dropping anything that fails to parse. This is deliberately
    permissive at the extraction stage - the STRICT check happens later, in the Decimal
    comparison, not here."""
    if not text:
        return ()
    candidates: list[Decimal] = []
    for match in _CANDIDATE_RE.finditer(text):
        value = tolerances.number(match.group(0))
        if value is not None:
            candidates.append(value)
    return tuple(candidates)


def csv_column_values(path: Path, column: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Parse `evidence_manifest.csv` and return `(values, errors)` for the requested
    published column. Never raises: a missing column or an unreadable file is reported as
    a named error (`missing_column:<column>`, `csv_unreadable:<path>`), not an exception."""
    path = Path(path)
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            fieldnames = reader.fieldnames or ()
            if column not in fieldnames:
                return (), (f"missing_column:{column}",)
            values = tuple(row.get(column, "") for row in reader)
            return values, ()
    except (OSError, csv.Error):
        return (), (f"csv_unreadable:{path}",)


def extract_memo_text(path: Path) -> tuple[str | None, str | None]:
    """Extract text from `decision_memo.pdf`. Tries `pdftotext -layout` (poppler-utils, an
    invoked binary, never imported, `shutil.which`-guarded, bounded timeout) first, falls
    back to an optional `pypdf` import inside a try/except ImportError, and otherwise
    returns `(None, "pdf_text_unavailable")` - an explicit reported condition, never a
    silent pass."""
    path = Path(path)

    pdftotext_bin = shutil.which("pdftotext")
    if pdftotext_bin is not None:
        try:
            proc = subprocess.run(
                [pdftotext_bin, "-layout", str(path), "-"],
                capture_output=True,
                timeout=_PDFTOTEXT_TIMEOUT_S,
                check=False,
            )
            if proc.returncode == 0:
                return proc.stdout.decode(errors="replace"), None
        except (subprocess.TimeoutExpired, OSError):
            pass

    try:
        import pypdf  # optional guarded import only, never a hard dependency
    except ImportError:
        pypdf = None

    if pypdf is not None:
        try:
            reader = pypdf.PdfReader(str(path))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            return text, None
        except Exception:
            pass

    return None, "pdf_text_unavailable"


# The four deliverable names `missing_in`/`required` ever name - matches
# `field_vocabulary.ARTIFACT_DELIVERABLES` exactly (this module deliberately does not
# import field_vocabulary, to keep its own dependency surface minimal - see module
# docstring's "all four deliverables named in BRIEF Sec4").
ALL_DELIVERABLE_NAMES: tuple[str, ...] = (
    "financial_model.xlsx",
    "assumptions_and_sources.xlsx",
    "evidence_manifest.csv",
    "decision_memo.pdf",
)


@dataclass(frozen=True)
class AgreementResult:
    """The outcome of one `agrees_across` check.

    `passed` is True only when `want` was matched within tolerance in every deliverable
    named in `required` (see `agrees_across`'s own `required` parameter). `missing_in`
    names EVERY deliverable where it was not found, required or not - kept complete for
    diagnostic value even when a non-required miss does not affect `passed`. `errors`
    carries structural problems (an untrusted model read, unavailable memo text) that are
    distinct from a genuine value mismatch.
    """

    field: str
    want: Decimal
    passed: bool
    missing_in: tuple[str, ...]
    errors: tuple[str, ...]


def agrees_across(
    field: str,
    want: Decimal,
    *,
    model_facts: "xlsx_raw.WorkbookFacts",
    assumptions_facts: "xlsx_raw.WorkbookFacts",
    manifest_values: tuple[str, ...],
    memo_text: str | None,
    tol: Decimal | None = None,
    trusted: bool = False,
    required: "frozenset[str] | tuple[str, ...] | None" = None,
) -> AgreementResult:
    """Assert `want` is matched within tolerance across every deliverable in `required`.

    `required` defaults to `ALL_DELIVERABLE_NAMES` (all four) - the original SCORE-05
    "loose-location, strict-value across every deliverable" contract this module's own
    docstring describes. Pass a narrower `required` (e.g. just `financial_model.xlsx` and
    `decision_memo.pdf`) for a check that is only meaningful across TWO deliverables -
    `case/scripts/rubric_registry.CROSS_FILE_TIE_NAMES` is exactly this narrower case: it
    is fanned out over the intersection of `DECISION_MEMO_FIELDS` and
    `FINANCIAL_MODEL_NAMED_RANGES` only, so `assumptions_and_sources.xlsx` and
    `evidence_manifest.csv` were never expected to carry these figures under their own
    published vocabulary (`ASSUMPTIONS_FIELDS`/`EVIDENCE_MANIFEST_COLUMNS` name a
    completely different set of fields) - requiring them unconditionally would make every
    `CROSS_FILE_TIE` item unpassable for a submission that follows the published contract
    exactly (found while wiring this into `case/scripts/score_outputs.py`, 04-05-PLAN.md
    Task 2, against `tools/submission_fixture.py`'s own positive fixture).

    `missing_in` still names EVERY deliverable that lacked the value, required or not -
    the narrowing affects only `passed`, never what gets reported, so a triager can still
    see the full picture.

    `trusted` MUST be True only when `model_facts` was obtained through
    `recalc.trusted_values` - `financial_model.xlsx`'s own cached values are never
    trustworthy (see `xlsx_raw.py`'s module docstring). Calling this with `trusted=False`
    (the safe default) records `"untrusted_values"` in `errors` and treats the model
    check as failed, rather than silently reading the submission's own cached numbers.
    """
    required_set = frozenset(required) if required is not None else frozenset(ALL_DELIVERABLE_NAMES)
    resolved_tol = tol if tol is not None else tolerances.tolerance_for(field)
    missing_in: list[str] = []
    errors: list[str] = []

    if not trusted:
        errors.append("untrusted_values")
        missing_in.append("financial_model.xlsx")
    else:
        model_numbers = xlsx_raw.cached_numbers(model_facts)
        if not any(tolerances.eq(got, want, resolved_tol) for got in model_numbers):
            missing_in.append("financial_model.xlsx")

    assumptions_numbers = xlsx_raw.cached_numbers(assumptions_facts)
    if not any(tolerances.eq(got, want, resolved_tol) for got in assumptions_numbers):
        missing_in.append("assumptions_and_sources.xlsx")

    manifest_numbers = tuple(
        v for v in (tolerances.number(raw) for raw in manifest_values) if v is not None
    )
    if not any(tolerances.eq(got, want, resolved_tol) for got in manifest_numbers):
        missing_in.append("evidence_manifest.csv")

    if memo_text is None:
        errors.append("memo_text_unavailable")
        missing_in.append("decision_memo.pdf")
    else:
        memo_numbers = numeric_candidates(memo_text)
        if not any(tolerances.eq(got, want, resolved_tol) for got in memo_numbers):
            missing_in.append("decision_memo.pdf")

    return AgreementResult(
        field=field,
        want=want,
        passed=not (set(missing_in) & required_set),
        missing_in=tuple(missing_in),
        errors=tuple(errors),
    )
