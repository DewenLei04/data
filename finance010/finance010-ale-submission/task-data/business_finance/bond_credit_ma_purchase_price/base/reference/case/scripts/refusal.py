"""case/scripts/refusal.py - paired refusal-grading primitives (SCORE-08, RESEARCH
Q5(b)) - the hardest problem in this case.

THE RULE THIS MODULE ENFORCES, STATED ONCE, UP FRONT: `gap_list` being non-empty is
NEVER a passing predicate, anywhere in this file. A submission that refuses every claim
must score full marks on nothing but the small, explicitly-enumerated
`REFUSAL_ELIGIBLE_CLAIMS` set (the genuinely-unresolvable-by-design claims, 3-5 of them)
and ZERO on every `Check_no_false_refusal` for the much larger `ANSWERABLE_CLAIMS` set
(50+), because it never supplies a single required definitive value. Symmetrically, a
submission that never refuses fails every `Check_refuse_correct` for the
genuinely-unresolvable claims. Two independent, paired, starred Checks per claim close
both exploits at once - see `test_blanket_refusal_scores_lower` in
`case/tests/test_cross_file.py` for the proof.

Claim identifiers are matched by REGISTERED IDENTITY (a `claim_id` string), never by
list position - a submission's `gap_list` can be reordered, padded, or partially
duplicated and this module must still resolve each claim correctly.

`REFUSAL_ELIGIBLE_CLAIMS` and `ANSWERABLE_CLAIMS` are never hard-coded in this module -
`claim_sets()` reads them from the loaded EXPECTED mapping (`tolerances.load_expected`),
so this file has zero business-specific claim identifiers of its own.
"""

from dataclasses import dataclass
from decimal import Decimal

import tolerances
from field_vocabulary import ALL_PUBLISHED_NAMES

# Every dict-key lookup this module hardcodes against a gap-list entry (`claim_id`,
# `gap_list`, `what_would_resolve`) is itself a published field_vocabulary name -
# asserted here so a future rename in field_vocabulary becomes a loud import-time
# failure here, never a silent lookup miss that reads as "claim not refused".
_LOOKUP_KEYS: tuple[str, ...] = ("claim_id", "gap_list", "what_would_resolve")
_drifted = [k for k in _LOOKUP_KEYS if k not in ALL_PUBLISHED_NAMES]
if _drifted:
    raise ValueError(f"refusal.py lookup key(s) {_drifted} are not published field_vocabulary names")

# Generic non-specific refusal boilerplate. A reason string that is nothing more than one
# of these (after casefolding) never counts as "correctly targeted" - see
# `check_refuse_correct`'s docstring.
BOILERPLATE_REASONS: frozenset[str] = frozenset(
    {
        "insufficient evidence",
        "insufficient evidence available",
        "insufficient evidence to support a definitive conclusion",
        "not enough information",
        "not enough information available",
        "unable to determine",
        "cannot be determined",
        "cannot be determined from the available evidence",
    }
)


@dataclass(frozen=True)
class CheckOutcome:
    """One scored Check's outcome for one claim.

    `starred` is always True for the Checks this module emits - both halves of the
    paired design are starred Checks per RESEARCH Q5(b); `score_refusal_pairs` never
    emits an unstarred Check.
    """

    check_id: str
    claim_id: str
    starred: bool
    passed: bool
    reason: str


def claim_sets(expected: dict) -> tuple[frozenset[str], frozenset[str]]:
    """Read `REFUSAL_ELIGIBLE_CLAIMS`/`ANSWERABLE_CLAIMS` from a loaded EXPECTED mapping.

    Asserts the two sets are disjoint and that the eligible set is the strictly smaller
    one (RESEARCH Q5(b): "ANSWERABLE_CLAIMS vastly outnumbers REFUSAL_ELIGIBLE_CLAIMS by
    design"). Raises `ValueError` naming the violation if either invariant fails - a
    broken claim-set contract must fail loudly, not degrade into an ungradeable rubric.
    """
    eligible = frozenset(expected["refusal_eligible_claims"])
    answerable = frozenset(expected["answerable_claims"])

    overlap = eligible & answerable
    if overlap:
        raise ValueError(
            f"REFUSAL_ELIGIBLE_CLAIMS and ANSWERABLE_CLAIMS overlap on {sorted(overlap)} "
            "- a claim must be exactly one of genuinely-unresolvable-by-design or "
            "answerable, never both"
        )
    if not len(eligible) < len(answerable):
        raise ValueError(
            f"REFUSAL_ELIGIBLE_CLAIMS ({len(eligible)}) must be strictly smaller than "
            f"ANSWERABLE_CLAIMS ({len(answerable)}) - this is the mechanism that makes "
            "blanket refusal lose"
        )
    return eligible, answerable


def _find_gap_entry(claim_id: str, gap_list) -> dict | None:
    for entry in gap_list:
        if entry.get("claim_id") == claim_id:
            return entry
    return None


def check_refuse_correct(
    claim_id: str, gap_list, required_evidence_tokens: tuple[str, ...]
) -> CheckOutcome:
    """Starred. Passes iff `claim_id` appears in `gap_list` (matched by registered
    identity) AND the entry's `what_would_resolve` reason text names at least one
    registered missing-evidence token (e.g. a specific Schedule or Note identifier) -
    a generic boilerplate reason (`BOILERPLATE_REASONS`) never passes, even though the
    claim was technically refused."""
    entry = _find_gap_entry(claim_id, gap_list)
    if entry is None:
        return CheckOutcome("Check_refuse_correct", claim_id, True, False, "claim_not_refused")

    reason_text = (entry.get("what_would_resolve") or "").strip()
    if not reason_text or reason_text.casefold() in BOILERPLATE_REASONS:
        return CheckOutcome("Check_refuse_correct", claim_id, True, False, "boilerplate_reason")

    if required_evidence_tokens and not any(
        token.casefold() in reason_text.casefold() for token in required_evidence_tokens
    ):
        return CheckOutcome(
            "Check_refuse_correct", claim_id, True, False, "reason_missing_evidence_token"
        )

    return CheckOutcome("Check_refuse_correct", claim_id, True, True, "ok")


def check_no_false_refusal(
    claim_id: str,
    gap_list,
    submitted_value: Decimal | None,
    expected_value: Decimal | None,
    tol: Decimal,
) -> CheckOutcome:
    """Starred, one per answerable claim. Passes iff `claim_id` is ABSENT from `gap_list`
    AND a definitive value is asserted for it matching `expected_value` within `tol`.
    This is the much larger set that a blanket-refusal submission always fails."""
    if _find_gap_entry(claim_id, gap_list) is not None:
        return CheckOutcome("Check_no_false_refusal", claim_id, True, False, "claim_was_refused")

    if submitted_value is None or expected_value is None:
        return CheckOutcome("Check_no_false_refusal", claim_id, True, False, "no_definitive_value")

    if not tolerances.eq(submitted_value, expected_value, tol):
        return CheckOutcome("Check_no_false_refusal", claim_id, True, False, "value_mismatch")

    return CheckOutcome("Check_no_false_refusal", claim_id, True, True, "ok")


def self_contradiction(claim_id: str, gap_list, definitive_claims) -> bool:
    """True when `claim_id` appears both in `gap_list` AND among `definitive_claims` (the
    set of claims the submission asserts a definitive conclusion for elsewhere) - the
    guard against crediting the correct half of a self-contradicting answer."""
    return _find_gap_entry(claim_id, gap_list) is not None and claim_id in definitive_claims


def score_refusal_pairs(expected: dict, submission: dict) -> tuple[CheckOutcome, ...]:
    """Emit one starred `Check_refuse_correct` per eligible claim and one starred
    `Check_no_false_refusal` per answerable claim.

    `expected` must carry, in addition to the `EXPECTED_SHAPE_KEYS` contract:
    `gap_reason_tokens: dict[claim_id, tuple[str, ...]]` (the registered missing-evidence
    tokens per eligible claim) and `claim_values: dict[claim_id, Decimal]` (the expected
    definitive value per answerable claim), plus an optional
    `claim_tolerances: dict[claim_id, Decimal]` (falls back to `tolerances.TOL_MONEY_USD`
    when absent for a claim).

    `submission` must carry `gap_list` (a sequence of gap-entry mappings),
    `definitive_claims` (the set of claim_ids asserted definitively elsewhere in the
    submission - drives the self-contradiction guard) and `claim_values` (submitted
    definitive values, if any).

    A claim appearing in both `gap_list` and `definitive_claims` forces BOTH of its
    paired outcomes to `passed=False` with `reason="self_contradiction"` - never the
    correct half of a self-contradicting answer.
    """
    eligible, answerable = claim_sets(expected)
    gap_list = list(submission.get("gap_list", ()))
    definitive_claims = set(submission.get("definitive_claims", ()))
    submitted_values = submission.get("claim_values", {})

    gap_reason_tokens = expected.get("gap_reason_tokens", {})
    claim_expected_values = expected.get("claim_values", {})
    claim_tolerances = expected.get("claim_tolerances", {})

    outcomes: list[CheckOutcome] = []

    for claim_id in sorted(eligible):
        if self_contradiction(claim_id, gap_list, definitive_claims):
            outcomes.append(
                CheckOutcome("Check_refuse_correct", claim_id, True, False, "self_contradiction")
            )
            continue
        tokens = gap_reason_tokens.get(claim_id, ())
        outcomes.append(check_refuse_correct(claim_id, gap_list, tokens))

    for claim_id in sorted(answerable):
        if self_contradiction(claim_id, gap_list, definitive_claims):
            outcomes.append(
                CheckOutcome("Check_no_false_refusal", claim_id, True, False, "self_contradiction")
            )
            continue
        expected_value = claim_expected_values.get(claim_id)
        submitted_value = submitted_values.get(claim_id)
        tol = claim_tolerances.get(claim_id, tolerances.TOL_MONEY_USD)
        outcomes.append(
            check_no_false_refusal(claim_id, gap_list, submitted_value, expected_value, tol)
        )

    return tuple(outcomes)
