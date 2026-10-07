"""case/scripts/alt_methods.py - per-metric alternative-method acceptance policy
(SCORE-12, RESEARCH Q5(c)).

Resolves, PER METRIC, ONCE, in `METHOD_POLICY` below - never by improvised
tolerance-widening at a call site - whether a scored metric has exactly one right answer
("pinned", governed by an explicit corpus clause) or a genuinely defensible alternative
("alternatives", requiring the memo to name which method it used so acceptance is never
silently ambiguous).

EARN-OUT BRANCH DECISION (RESEARCH Q5(c)'s planner decision rule, resolved before writing
this table): Phase 1 PINS the earn-out method. `DESIGN-NUMERIC.md`'s "Earn-out
discount-rate convention" bullet (also carried into
`01-05-SUMMARY.md`'s key-decisions: "Earn-out discount convention pinned explicitly:
EARNOUT_DISCOUNT_RATE_OPERATIONAL over EARNOUT_PERIOD_COUNT years, with the revenue gate
applied as a per-scenario knockout") fixes a single probability-weighted 3-scenario tree
(`tools.facts_spa.EARNOUT_SCENARIOS`) with an explicit discount-rate convention
distinguishing the target's own operational rate (while the payout is still contingent)
from the buyer-credit rate (once fixed and due) - a genuine governing clause, not an
absence of a rule. No option-based earn-out valuation method exists anywhere in Phase 1's registries: the
only "option" vocabulary in `tools/solve_reference.py` is the corporate note's embedded
call option (`option_cost`/`option_adjusted_spread`, CORPUS-C1 Sec5's OAS relative-value
family) - an unrelated bond-math concept, never an earn-out valuation method. Neither
`tools/facts_spa.py`'s `EARNOUT_SCENARIOS` registry nor `tools/mk_expected.py`'s derived
earn-out figure defines or references a second (option-pricing) earn-out methodology.
Per RESEARCH Q5(c): "If Phase 1 pins the method: treat earn-out as single-right-answer
(simpler, consistent with every other metric)." Registered `earnout_expected_value` as
`mode="pinned"` below. `METHOD_POLICY` therefore has zero `"alternatives"` entries in
this build - consistent with `ARCHITECTURE.md`'s observation that finance001 itself has
zero live instances of genuine multi-method acceptance. The `"alternatives"` code path
(`accepts()`'s `or`-across-`accepted_keys` branch, the module-level
`len(accepted_keys) >= 2` / `naming_required` assertions) is still fully implemented and
tested (`case/tests/test_cross_file.py`) against synthetic policies, so a future corpus
change that genuinely unpins a metric needs no code change here - only a new
`MethodPolicy` row.
"""

from dataclasses import dataclass, fields
from decimal import Decimal

from field_vocabulary import ALL_PUBLISHED_NAMES
from tolerances import eq, tolerance_for


@dataclass(frozen=True)
class MethodPolicy:
    """One metric's method-acceptance policy.

    `mode` is `"pinned"` (exactly one right answer, `accepted_keys` empty) or
    `"alternatives"` (>=2 genuinely defensible methods, each named in `accepted_keys`,
    `naming_required` always True). `rationale` names the governing clause (for
    `"pinned"`) or the reason no single method is defensible (for `"alternatives"`).

    Deliberately has NO tolerance field of its own - see the module-level assertion below.
    Every tolerance lookup for a metric goes through `tolerances.tolerance_for`, never a
    policy-carried override, so a metric's tolerance and its method policy can never
    silently drift apart.
    """

    field: str
    mode: str
    rationale: str
    accepted_keys: tuple[str, ...]
    naming_required: bool


METHOD_POLICY: dict[str, MethodPolicy] = {
    "make_whole_amount": MethodPolicy(
        field="make_whole_amount",
        mode="pinned",
        rationale="The indenture's own Applicable Premium definition discounts to the "
        "par call date, never legal maturity - a single governing clause, no defensible "
        "alternative discounting date.",
        accepted_keys=(),
        naming_required=False,
    ),
    "working_capital_adjustment_amount": MethodPolicy(
        field="working_capital_adjustment_amount",
        mode="pinned",
        rationale="The SPA's own Accounting Principles schedule is the sole governing "
        "hierarchy for the working-capital bridge; no alternative schedule is defensible.",
        accepted_keys=(),
        naming_required=False,
    ),
    "corporate_note_spread_to_treasury_bp": MethodPolicy(
        field="corporate_note_spread_to_treasury_bp",
        mode="pinned",
        rationale="Per-instrument day-count convention (30/360 for the corporate note vs. "
        "Actual/Actual ICMA for the Treasury curve) is fixed bond-math convention, not a "
        "modelling choice.",
        accepted_keys=(),
        naming_required=False,
    ),
    "locked_box_completion_accounts_delta": MethodPolicy(
        field="locked_box_completion_accounts_delta",
        mode="pinned",
        rationale="Which SPA mechanism actually governs closing (completion accounts vs. "
        "the superseded locked-box precedent) is a factual determination from the "
        "executed agreement, not a method choice.",
        accepted_keys=(),
        naming_required=False,
    ),
    "cecl_expected_credit_loss": MethodPolicy(
        field="cecl_expected_credit_loss",
        mode="pinned",
        rationale="The instrument's disclosed balance-sheet classification (HTM vs. AFS) "
        "fixes the CECL measurement basis; it is a fact to be read from the audited "
        "financial statements, not a method to be chosen.",
        accepted_keys=(),
        naming_required=False,
    ),
    "earnout_expected_value": MethodPolicy(
        field="earnout_expected_value",
        mode="pinned",
        rationale="Phase 1 pins the earn-out to a single probability-weighted scenario "
        "tree with an explicit discount-rate convention (DESIGN-NUMERIC.md's "
        "'Earn-out discount-rate convention' bullet; 01-05-SUMMARY.md key-decisions) and "
        "never builds an option-based alternative anywhere in the registries - see this "
        "module's docstring for the full resolution of RESEARCH Q5(c)'s planner decision "
        "rule.",
        accepted_keys=(),
        naming_required=False,
    ),
}

# --- Module-level invariants - fail loudly on any drift, never at call time ---

_VALID_MODES = frozenset({"pinned", "alternatives"})


def _validate_policy_table(table: dict[str, MethodPolicy]) -> None:
    """The validation logic run unconditionally against `METHOD_POLICY` below, and
    exposed here (not only inlined at import time) so `case/tests/test_cross_file.py` can
    exercise every violation branch directly against a synthetic table, without needing
    to break the real `METHOD_POLICY` or reload this module in a corrupted state. Raises
    `ValueError` naming the violation; never returns a partial result."""
    unpublished = set(table) - ALL_PUBLISHED_NAMES
    if unpublished:
        raise ValueError(
            f"METHOD_POLICY key(s) {sorted(unpublished)} are not published "
            "field_vocabulary names"
        )

    for field, policy in table.items():
        if policy.mode not in _VALID_MODES:
            raise ValueError(f"METHOD_POLICY[{field!r}].mode {policy.mode!r} not in {_VALID_MODES}")
        if policy.mode == "alternatives":
            if len(policy.accepted_keys) < 2:
                raise ValueError(
                    f"METHOD_POLICY[{field!r}] is 'alternatives' but has fewer than two "
                    f"accepted_keys: {policy.accepted_keys!r}"
                )
            if not policy.naming_required:
                raise ValueError(
                    f"METHOD_POLICY[{field!r}] is 'alternatives' but naming_required is "
                    "False - an alternatives metric must always require the memo to name "
                    "which method it used"
                )

    if any("tol" in f.name.lower() for f in fields(MethodPolicy)):
        raise ValueError(
            "MethodPolicy must never carry its own tolerance override - route every "
            "tolerance lookup through tolerances.tolerance_for"
        )


_validate_policy_table(METHOD_POLICY)


def accepts(
    field: str,
    submitted: Decimal,
    expected_entry: Decimal | dict[str, Decimal],
    tol: Decimal | None = None,
) -> bool:
    """Decide whether `submitted` is an accepted answer for `field` under its
    `METHOD_POLICY` entry.

    For `"pinned"`: `expected_entry` must be the single expected `Decimal`; passes iff it
    matches within tolerance. For `"alternatives"`: `expected_entry` must be a mapping
    from each of the policy's `accepted_keys` to its `Decimal` result; passes iff
    `submitted` matches ANY one of them within tolerance - never by widening the
    tolerance itself.
    """
    policy = METHOD_POLICY[field]
    resolved_tol = tol if tol is not None else tolerance_for(field)

    if policy.mode == "pinned":
        if not isinstance(expected_entry, Decimal):
            raise TypeError(f"'pinned' policy for {field!r} expects a single Decimal expected value")
        return eq(submitted, expected_entry, resolved_tol)

    if not isinstance(expected_entry, dict):
        raise TypeError(
            f"'alternatives' policy for {field!r} expects a mapping of accepted_keys to "
            "Decimal results"
        )
    for key in policy.accepted_keys:
        candidate = expected_entry.get(key)
        if isinstance(candidate, Decimal) and eq(submitted, candidate, resolved_tol):
            return True
    return False


def requires_method_naming(field: str) -> bool:
    """True iff `field` admits MORE THAN ONE defensible method (`mode == "alternatives"`)
    - the only situation in which naming is what disambiguates which result governs.

    04-08-PLAN.md Task 2 restates this function's semantics. It used to return
    `METHOD_POLICY[field].naming_required`, which - for every field live in this build,
    all `"pinned"` - is `False`. That is an answer the scorer must CONTRADICT, not merely
    decline to use: `score_outputs._evaluate_policy` requires the governing method be
    named for every `METHOD_POLICY` field UNCONDITIONALLY, regardless of `mode`, because
    the rubric's two `METHOD_POLICY` items demand naming outright (04-06-PLAN.md's own
    finding - gating on the old `naming_required` reading would have made naming optional
    exactly where the rubric requires it). A helper whose only two live inputs return an
    answer the caller must contradict is a hazard: it looks authoritative and is not, and
    the next reader who wires it in reintroduces the bug 04-06 deliberately avoided.

    Restated so a caller CAN honour the return value without contradiction: this reports
    whether `field` has a genuine choice of method to disambiguate BETWEEN, never whether
    naming is optional for it. `_evaluate_policy` still does not call this function - it
    is not a gating condition on whether the scorer demands a name, it is an
    informational fact about the field's own policy shape (identical to checking
    `METHOD_POLICY[field].mode == "alternatives"` directly, spelled out here as its own
    named accessor because policy internals should not leak to every call site that only
    wants this one yes/no question). Values are unchanged for both fields this build
    exercises today (`False` for both pinned metrics; `True` for a synthetic
    `"alternatives"` policy - see `case/tests/test_cross_file.py::AltMethodsTests`).
    """
    return METHOD_POLICY[field].mode == "alternatives"
