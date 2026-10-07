"""case/scripts/tolerances.py - the Decimal tolerance table, text-to-Decimal
normalisation, and the `EXPECTED.json` shape contract (SCORE-05, SCORE-07, T-02-08).

Every money comparison anywhere in this scorer's primitive layer must go through `eq()`
here, and every tolerance must be looked up by a published `field_vocabulary` name via
`tolerance_for()` - never a private shorthand, never a bare numeric literal at the call
site, and never a `float`. `eq()` enforces the last of those at runtime: it raises
`TypeError` on any non-Decimal argument, so a stray `float` comparison is a hard failure
at the exact call site that introduced it, not a silent precision-loss bug three modules
downstream.

`TOL` reconciles against `case/task_card.json`'s `evaluation.tolerances` (the published
tolerance CONTRACT) - this module implements that contract for the specific metrics it
names, it never restates or overrides the contract itself.
"""

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType

import field_vocabulary as fv_module
from field_vocabulary import ALL_PUBLISHED_NAMES

# --- The published tolerance contract, read from task_card.json (the single source of truth) ---
#
# These values are NOT restated here. `case/task_card.json`'s `evaluation.tolerances` is the
# contract the agent is shown, and this module resolves against it by reading it.
#
# Why it is read rather than transcribed: the constants below were previously hand-written, and
# they had drifted into contradicting the contract badly. The card publishes a +/-$2,500 currency
# tolerance; the transcribed constant was +/-$0.01 - a scorer 250,000x stricter than what the
# prompt promises. An agent that computed a figure $500 off, well inside the tolerance it was
# given, would have been marked wrong. That is not difficulty, it is a rubric/prompt mismatch
# (PITFALLS.md 3.5), and it depresses a blind-probe score for a reason that has nothing to do
# with analytical skill. Deriving from the card makes that class of divergence impossible rather
# than merely discouraged.
_TASK_CARD_PATH = Path(__file__).resolve().parents[1] / "task_card.json"


def _load_quantity_classes() -> dict[str, Decimal]:
    """Map `quantity_class` -> Decimal tolerance, from the published contract.

    `exact_match_only` (dates) becomes `Decimal("0")`: the same semantics, expressed in the one
    numeric type this module permits.
    """
    try:
        card = json.loads(_TASK_CARD_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:  # a build defect, not adversarial input
        raise RuntimeError(
            f"cannot read the published tolerance contract at {_TASK_CARD_PATH}: {exc}"
        ) from None

    classes: dict[str, Decimal] = {}
    for row in card.get("evaluation", {}).get("tolerances", ()):
        raw = row.get("tolerance")
        # Decimal(str(...)) - never Decimal(float). json parses 0.02 to a binary float, and
        # Decimal(0.02) would carry its representation error into every comparison.
        classes[row["quantity_class"]] = (
            Decimal(0) if raw == "exact_match_only" else Decimal(str(raw))
        )
    if not classes:
        raise RuntimeError(
            f"{_TASK_CARD_PATH} declares no evaluation.tolerances - the tolerance contract is the "
            "scorer's fairness guarantee and an empty one silently makes every comparison exact"
        )
    return classes


QUANTITY_CLASS_TOLERANCE: dict[str, Decimal] = _load_quantity_classes()

CLASS_CURRENCY = "currency_amounts"
CLASS_YIELD_PCT = "yields_coupons_spreads_pct"
CLASS_YIELD_BP = "yields_coupons_spreads_bp"
CLASS_BOND_PRICE = "bond_prices"
CLASS_LEVERAGE = "leverage_coverage_ratios"
CLASS_MARGIN_PCT = "percentages_and_margins"
CLASS_DATE = "dates"

# --- Named constants, now derived from the contract rather than transcribed beside it ---

TOL_MONEY_USD = QUANTITY_CLASS_TOLERANCE[CLASS_CURRENCY]
TOL_RATE = QUANTITY_CLASS_TOLERANCE[CLASS_YIELD_PCT]
TOL_BPS = QUANTITY_CLASS_TOLERANCE[CLASS_YIELD_BP]
TOL_PRICE = QUANTITY_CLASS_TOLERANCE[CLASS_BOND_PRICE]
TOL_LEVERAGE = QUANTITY_CLASS_TOLERANCE[CLASS_LEVERAGE]
TOL_MARGIN = QUANTITY_CLASS_TOLERANCE[CLASS_MARGIN_PCT]
TOL_DATE_DAYS = QUANTITY_CLASS_TOLERANCE[CLASS_DATE]

# A count is exact by nature and has no published class - an iteration count of "3 +/- 2" is not a
# weaker answer, it is a different one.
TOL_COUNT = Decimal(0)
TOL_MONEY_THOUSANDS = Decimal("1000")

# --- Field -> quantity class. Every value-asserting name resolves through here. ---
#
# Keys are published `field_vocabulary` names AND the answer table's own keys, because the rubric
# asserts values under both (see `field_vocabulary.ANSWER_KEY_TO_PUBLISHED_FIELD`). Assigning a
# class is a judgement about what KIND of quantity a field is, so it is written out per field
# rather than guessed from a name substring - `post_close_leverage` is a ratio and
# `redemption_cost` is currency, and no naming convention distinguishes them reliably.
FIELD_CLASS: dict[str, str] = {
    # --- currency ---
    "working_capital_adjustment_amount": CLASS_CURRENCY,
    "working_capital_adjustment": CLASS_CURRENCY,
    "earnout_expected_value": CLASS_CURRENCY,
    "earnout_expected_payout": CLASS_CURRENCY,
    "locked_box_completion_accounts_delta": CLASS_CURRENCY,
    # --- 04.1-02-PLAN.md Task 1: the earn-out chain intermediates - the same currency
    # class as every other dollar figure; no new class, no narrowing. ---
    "earnout_scenario_payout_downside": CLASS_CURRENCY,
    "earnout_scenario_payout_base": CLASS_CURRENCY,
    "earnout_scenario_payout_upside": CLASS_CURRENCY,
    "earnout_expected_undiscounted": CLASS_CURRENCY,
    "make_whole_amount": CLASS_CURRENCY,
    "make_whole_amount_due": CLASS_CURRENCY,
    "make_whole_accrued_interest": CLASS_CURRENCY,
    "make_whole_pv_remaining": CLASS_CURRENCY,
    "cecl_expected_credit_loss": CLASS_CURRENCY,
    "coc_put_amount_due": CLASS_CURRENCY,
    "consolidated_ebitda": CLASS_CURRENCY,
    "equity_consideration": CLASS_CURRENCY,
    "equity_consideration_at_closing": CLASS_CURRENCY,
    "spa_net_debt": CLASS_CURRENCY,
    "incremental_term_loan": CLASS_CURRENCY,
    "redemption_cost": CLASS_CURRENCY,
    "change_of_control_put_price": CLASS_CURRENCY,
    # --- spreads in basis points ---
    "corporate_note_spread_to_treasury_bp": CLASS_YIELD_BP,
    # 04.1-03-PLAN.md Task 1: the relative-value chain's swap-curve leg - same published
    # class as every other basis-point spread; no new class, no narrowing.
    "i_spread_bp": CLASS_YIELD_BP,
    # --- rates quoted in percent ---
    "make_whole_discount_rate": CLASS_YIELD_PCT,
    "muni_tax_equivalent_yield_pct": CLASS_YIELD_PCT,
    # 04.1-03-PLAN.md Task 1: the relative-value chain's yield leg - same published
    # class as every other percent-quoted yield; no new class, no narrowing.
    "yield_to_worst_pct": CLASS_YIELD_PCT,
    # --- leverage / coverage ratios ---
    "post_close_leverage": CLASS_LEVERAGE,
    "post_close_net_leverage": CLASS_LEVERAGE,
    "credit_policy_headroom": CLASS_LEVERAGE,
    "covenant_net_debt": CLASS_CURRENCY,
    "enterprise_value": CLASS_CURRENCY,
    "closing_working_capital": CLASS_CURRENCY,
    "working_capital_peg": CLASS_CURRENCY,
}

# Exact-by-nature fields with no published quantity class. A count or a categorical regime is not
# a measurement, so "within tolerance" is meaningless for it: an iteration count of 3 +/- 2 is not
# a less precise answer, it is a different one. Kept as an explicit set rather than folded into
# FIELD_CLASS so that "no class applies" never looks like "a class was forgotten".
EXACT_FIELDS: frozenset[str] = frozenset({
    # `outer_iterations` is deliberately ABSENT: it is a solver internal, not an answer. It is
    # graded nowhere and belongs in EXPECTED.json's ungraded `meta` section - an agent whose fixed
    # point converges in a different number of passes than ours is not wrong. See ERRATA E4.
    "ebitda_regime",
    "put_triggered",
    "rating_gate_crossed",
    "make_whole_par_floor_binding",
    # Categorical conclusions and labels. "Completion accounts" and "locked box" are different
    # answers, not nearby ones, so a numeric tolerance is meaningless for them.
    "change_of_control_put_conclusion",
    "governing_price_mechanism",
    "recommendation",
    "redemption_path_selected",
    "relative_value_conclusion",
    "earnout_valuation_method",
    "make_whole_discount_target",
    "valuation_date",
    "document_effective_date",
    # 04.1-02-PLAN.md Task 1: the earn-out discount horizon is a count (a period count
    # plus a day-counted offset, both computed deterministically from stated corpus
    # facts), never a measurement with rounding-driven uncertainty - no published
    # quantity class fits it, and none should: an offset of 26 days is either read
    # correctly or it is a different answer, exactly like an iteration count or a
    # regime label. `TOL_COUNT` documents the same reasoning for counts generally.
    "earnout_discount_horizon_years",
})

# --- The tolerance table, now DERIVED from the field->class map, not hand-written ---

TOL: dict[str, Decimal] = {
    **{field: Decimal(0) for field in EXACT_FIELDS},
    **{field: QUANTITY_CLASS_TOLERANCE[cls] for field, cls in FIELD_CLASS.items()},
}

_unknown_classes = {c for c in FIELD_CLASS.values() if c not in QUANTITY_CLASS_TOLERANCE}
if _unknown_classes:
    raise ValueError(
        f"FIELD_CLASS references quantity class(es) {sorted(_unknown_classes)} that "
        f"{_TASK_CARD_PATH.name} does not publish; the scorer may only use published classes"
    )

_overlap = EXACT_FIELDS & set(FIELD_CLASS)
if _overlap:
    raise ValueError(
        f"field(s) {sorted(_overlap)} are both class-mapped and declared exact - one field, one "
        "comparison rule"
    )

# Every tolerance key must resolve through `field_vocabulary` - never a private shorthand. A key
# may be a published field name OR an answer-table key that the vocabulary's declared bridge maps
# to a published name; the second form exists because the reference solver names its outputs for
# the calculation (`make_whole_amount_due`) while the agent publishes the deliverable field
# (`make_whole_amount`), and the scorer must read one and grade against the other. What is
# forbidden is a key that resolves to neither, which is how a tolerance starts governing a figure
# nobody is asked to report. `tools/check_derivable.py` gates the same property from the rubric side.
_BRIDGE = dict(getattr(fv_module, "ANSWER_KEY_TO_PUBLISHED_FIELD", {}) or {})
_unresolvable = {
    key for key in TOL
    if key not in ALL_PUBLISHED_NAMES and _BRIDGE.get(key) not in ALL_PUBLISHED_NAMES
}
if _unresolvable:
    raise ValueError(
        f"tolerances.TOL key(s) {sorted(_unresolvable)} resolve to no published "
        "field_vocabulary name, directly or through ANSWER_KEY_TO_PUBLISHED_FIELD - every "
        "tolerance key must resolve through field_vocabulary, never a private shorthand"
    )


def tolerance_for(field: str) -> Decimal:
    """Look up the Decimal tolerance for a published field name. Raises `KeyError` naming
    every valid key when `field` is not registered - never silently widens or invents a
    tolerance at the call site."""
    try:
        return TOL[field]
    except KeyError:
        raise KeyError(
            f"no tolerance registered for {field!r}; valid keys: {sorted(TOL)}"
        ) from None


# --- Text-to-Decimal normalisation ---

_NON_NUMERIC_TOKENS = frozenset({"n/a", "na", "none", "-", "--", "tbd", ""})


def number(text: str | None) -> Decimal | None:
    """Normalise a free-text numeric token to `Decimal`, handling `$` prefixes, thousands
    separators, whitespace, a trailing `%` (divided by 100), and accounting-style
    parentheses as a negative sign. Returns `None` on any non-numeric token - never
    raises, this function is meant to be run over untrusted prose/CSV/cell text."""
    if text is None:
        return None
    s = text.strip()
    if not s:
        return None

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()

    percent = False
    if s.endswith("%"):
        percent = True
        s = s[:-1].strip()

    s = s.replace("$", "").replace(",", "").strip()

    if s.casefold() in _NON_NUMERIC_TOKENS:
        return None

    try:
        value = Decimal(s)
    except InvalidOperation:
        return None

    if negative:
        value = -value
    if percent:
        value = value / Decimal("100")
    return value


def eq(got: Decimal, want: Decimal, tol: Decimal) -> bool:
    """`abs(got - want) <= tol`, over Decimal arguments only. Raises `TypeError` on any
    non-Decimal argument (including `bool`, `int`, or `float`) - this is the no-float
    gate every downstream numeric-agreement check in this scorer must pass through."""
    if not (isinstance(got, Decimal) and isinstance(want, Decimal) and isinstance(tol, Decimal)):
        raise TypeError(
            "eq() accepts Decimal arguments only, got "
            f"({type(got).__name__}, {type(want).__name__}, {type(tol).__name__})"
        )
    return abs(got - want) <= tol


# --- The EXPECTED.json shape contract ---

EXPECTED_SHAPE_KEYS: tuple[str, ...] = (
    "schema_version",
    "valuation_date",
    "metrics",
    "refusal_eligible_claims",
    "answerable_claims",
)


def validate_expected_shape(obj: object) -> list[str]:
    """Return a list of named shape errors (empty iff `obj` matches the EXPECTED.json
    shape contract). Named error strings: `missing_key:<k>`, `bad_type:<k>`,
    `metric_not_published:<name>`. Never raises."""
    if not isinstance(obj, dict):
        return ["bad_type:root"]

    errors: list[str] = []
    for key in EXPECTED_SHAPE_KEYS:
        if key not in obj:
            errors.append(f"missing_key:{key}")

    if "schema_version" in obj and not isinstance(obj["schema_version"], str):
        errors.append("bad_type:schema_version")
    if "valuation_date" in obj and not isinstance(obj["valuation_date"], str):
        errors.append("bad_type:valuation_date")

    if "metrics" in obj:
        metrics = obj["metrics"]
        if not isinstance(metrics, dict):
            errors.append("bad_type:metrics")
        else:
            for name in metrics:
                if name not in ALL_PUBLISHED_NAMES:
                    errors.append(f"metric_not_published:{name}")

    for key in ("refusal_eligible_claims", "answerable_claims"):
        if key in obj and not isinstance(obj[key], list):
            errors.append(f"bad_type:{key}")

    return errors


def load_expected(path: Path) -> MappingProxyType:
    """Parse `EXPECTED.json`, validate its shape, convert every metric value through
    `number()`, and return a read-only mapping. Raises `ValueError` naming every shape
    error if the shape contract is violated - unlike a submission parse, this file is the
    scorer's own private contract, so a shape violation here is a build-time defect, not
    an adversarial input, and must fail loudly rather than degrade gracefully."""
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = validate_expected_shape(obj)
    if errors:
        raise ValueError("EXPECTED.json shape errors: " + ", ".join(errors))

    result = dict(obj)
    result["metrics"] = {name: number(str(value)) for name, value in obj["metrics"].items()}
    return MappingProxyType(result)
