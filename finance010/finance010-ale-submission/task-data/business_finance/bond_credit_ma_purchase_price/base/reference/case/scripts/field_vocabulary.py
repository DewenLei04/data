"""case/scripts/field_vocabulary.py - the single machine-readable source of truth for
every agent-visible output field name in finance010 / T010 (SCORE-04).

Why this file exists: schema/scorer name drift caused the majority of the reference
case's blind-trial score losses (ARCHITECTURE.md's flagged highest-risk finding). This
module freezes the vocabulary once, before any scorer, rubric, prompt or task_card line
exists. Every later Phase 2 plan (02-02 prompt/task-card, 02-04 scorer core, 02-05
grounding/rubric) imports names from here - never restates a spelling by hand.

Three consumers, one spelling:
  1. `00_task/output_schemas/*.schema.json` (Task 2 of this plan) - the agent-visible
     contract; every JSON Schema `properties` key must be a name published here.
  2. `case/scripts/rubric_registry.py` / `case/scripts/score_outputs.py` (02-04/02-05,
     future plans) - the scorer; every lookup must resolve through
     `ALL_PUBLISHED_NAMES`, never a private shorthand.
  3. `tools/check_field_names.py` (Task 3 of this plan) - the enforcing gate that proves
     (1) and (2) never drift from this module, in both directions.

Design rules:
- Stdlib only: `dataclasses` and `typing`. No I/O, no third-party imports, ever.
- No package plumbing: this file is loaded both as a direct script-relative import (by
  future `case/scripts/` siblings) and by explicit path via
  `importlib.util.spec_from_file_location` (by `tools/check_field_names.py`, which lives
  outside any package rooted at `case/`). Do not add `case/__init__.py` or
  `case/scripts/__init__.py` to "fix" this - the explicit-path load is intentional.
- Every published name matches `^[a-z][a-z0-9_]*$` - the same shape the AST drift-check
  in `tools/check_field_names.py` looks for, and the shape Excel named ranges use here
  (Excel named ranges are case-insensitive; matching spellings are what make cross-file
  agreement mechanically checkable).
- No example, default or literal VALUE belongs in this file - only field NAMES, KINDS,
  UNITS and instructional descriptions. A description tells the agent what a field is,
  never what its value is. Concrete numbers live in the private `reference/EXPECTED.json`
  (Phase 1 Wave 4, not yet built) and in `tools/facts_bond.py` / `tools/facts_spa.py` /
  `tools/facts_shared.py` (the Phase 1 registries) - this module must never import those
  or echo any of their literals.

CSV flattening rule (read this before touching `EVIDENCE_MANIFEST_COLUMN_ORDER`):
`evidence_manifest.csv` is a flat table; it cannot nest a `provenance` object the way the
JSON-shaped schemas can. So the shared provenance envelope (`PROVENANCE_FIELDS`) appears
two different ways depending on the deliverable: nested under a `provenance` property in
every JSON-shaped schema (`decision_memo.schema.json` via its gap list,
`assumptions_and_sources.schema.json`, `refusal_gap_list.schema.json`), and flattened -
its six non-`provenance` columns appended directly - as the tail of
`EVIDENCE_MANIFEST_COLUMN_ORDER` for the CSV. The `provenance` name itself (the JSON
object-valued property) is therefore excluded from the CSV column order; only its six
leaf fields appear there.
"""

import re
from dataclasses import dataclass

# NOTE: deliberately no `from __future__ import annotations` here. This module is
# loaded two ways: as a normal import, and by explicit path via
# `importlib.util.spec_from_file_location` (by `tools/check_field_names.py`) WITHOUT
# registering the module in `sys.modules` first. Under that second loading path,
# `from __future__ import annotations` triggers a CPython `dataclasses` bug on 3.10:
# `_is_type()`'s string-annotation resolution does `sys.modules.get(cls.__module__)`,
# which is `None` for an unregistered module, raising AttributeError at class-definition
# time. Python 3.10 supports `str | None` natively (PEP 604) without the future import,
# so omitting it is the fix, not a workaround.

# --- The FieldSpec contract ---

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_ALLOWED_KINDS: tuple[str, ...] = ("string", "number", "date", "array", "object")


@dataclass(frozen=True)
class FieldSpec:
    """One published, agent-visible field name.

    Fields:
        name: the published field name, exactly as it must appear agent-side (schema
            property key, Excel named range, CSV column header, or gap-entry key).
        deliverable: one of `DELIVERABLES` - the final artifact this name is published
            in, or the sentinel `"shared"` for names that live in the shared provenance
            envelope or the shared gap-list shape.
        kind: one of `"string"`, `"number"`, `"date"`, `"array"`, `"object"`. A field
            whose agent-visible JSON Schema `type` is `boolean` (there is exactly one:
            `definitive_conclusion_withheld`) is catalogued here as `"string"` - this
            module's kind vocabulary intentionally has five members, not six, and the
            schema is free to declare a narrower JSON type than this coarse category.
        unit: `"USD"`, `"bp"`, `"pct"`, `"x"`, `"ISO-8601"`, or `None` for fields with no
            physical unit (labels, identifiers, structured objects/arrays).
        description: a one-line instruction to the agent about what the field IS. Never
            states or implies what the field's value should be.
    """

    name: str
    deliverable: str
    kind: str
    unit: str | None = None
    description: str = ""


# --- The answer-key bridge ---
#
# `reference/EXPECTED.json` names its values for the CALCULATION that produced them
# (`make_whole_amount_due`), while the agent publishes the DELIVERABLE FIELD
# (`make_whole_amount`). The scorer must read the second and grade against the first, so the
# correspondence has to be written down exactly once, here, where the published names live.
#
# This bridge is not a convenience. Without it the rubric asserts values under keys the agent is
# never shown, every such item is unpassable, and because the answer-value family is core, the G2
# gate pins every submission at 0.0 - the strongest model and an empty directory score identically.
# `tools/check_derivable.py` gates the property directly: every graded answer key must resolve,
# through this map or by being a published name already, to something the agent is asked to report.
#
# A key absent from this map and absent from the vocabulary is a DEFECT, not an omission. The fix is
# to publish the field (extend the deliverable schema so the agent must report it) or to stop
# grading the value (move it to the ungraded `meta` section) - never to quietly tolerate it.
ANSWER_KEY_TO_PUBLISHED_FIELD: dict[str, str] = {
    # bond half
    "coc_put_amount_due": "change_of_control_put_price",
    "make_whole_amount_due": "make_whole_amount",
    "put_triggered": "change_of_control_put_conclusion",
    # spa half
    "earnout_expected_payout": "earnout_expected_value",
    "equity_consideration": "equity_consideration_at_closing",
    "working_capital_adjustment": "working_capital_adjustment_amount",
    # coupled half
    "post_close_leverage": "post_close_net_leverage",
}


# --- Deliverables ---

# READ THIS BEFORE ITERATING `DELIVERABLES`.
#
# `DELIVERABLES` is the allowed-values domain for `FieldSpec.deliverable`, and its last member
# is the SENTINEL `"shared"`, not a file. Any consumer that needs "the files the agent must
# produce" must iterate `ARTIFACT_DELIVERABLES` instead.
#
# This is not a hypothetical distinction. Deriving a required-artifact list from `DELIVERABLES`
# puts a file named `shared` in the G0 presence gate; no agent can ever produce it, so G0 fails
# for every submission and every score is 0.0 regardless of quality. A blind probe against that
# scorer reports 0% for an infrastructural reason that looks exactly like difficulty. The two
# tuples are therefore named separately, and `ARTIFACT_DELIVERABLES` is the one with no sentinel
# in it.
SHARED_SENTINEL: str = "shared"

ARTIFACT_DELIVERABLES: tuple[str, ...] = (
    "decision_memo.pdf",
    "financial_model.xlsx",
    "assumptions_and_sources.xlsx",
    "evidence_manifest.csv",
)

DELIVERABLES: tuple[str, ...] = ARTIFACT_DELIVERABLES + (SHARED_SENTINEL,)

_MEMO = "decision_memo.pdf"
_MODEL = "financial_model.xlsx"
_ASSUMPTIONS = "assumptions_and_sources.xlsx"
_EVIDENCE = "evidence_manifest.csv"
_SHARED = SHARED_SENTINEL


# --- DECISION_MEMO_FIELDS (23) ---

DECISION_MEMO_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "recommendation", _MEMO, "string",
        description="The memo's single overall go/no-go or proceed-with-conditions "
        "recommendation label, drawn from this field's enum of decision labels only.",
    ),
    FieldSpec(
        "valuation_date", _MEMO, "date", unit="ISO-8601",
        description="The fixed as-of date every figure in this memo is anchored to; "
        "nothing dated after it may move a valuation-date figure.",
    ),
    # --- Fields published because the rubric grades them (see ERRATA E4) ---
    #
    # Each of these seven was a graded answer value with no published home, which made its rubric
    # item unpassable and - the family being core - pinned every submission's score at 0.0. They are
    # published rather than dropped from grading because every one of them is a figure a real credit
    # memo states: the covenant EBITDA is the denominator of the leverage test, the accrued interest
    # and PV components are required lines of any redemption calculation, and the two trigger
    # conclusions are the analysis itself. The numeric ones are also `_MODEL` named ranges, so they
    # become CROSS_FILE_TIE items and must agree between the memo and the workbook.
    FieldSpec(
        "consolidated_ebitda", _MEMO, "number", unit="USD",
        description="Consolidated EBITDA as defined by the credit agreement's own definition, "
        "which governs the covenant test by its terms - not any similarly-named Adjusted EBITDA "
        "from a quality-of-earnings report or a compliance certificate.",
    ),
    FieldSpec(
        "ebitda_regime", _MEMO, "string",
        description="Which synergy-addback regime governs the Consolidated EBITDA figure: the "
        "capped regime, where the credit agreement's proviso binds the revenue-enhancement "
        "component, or the uncapped regime, where it does not.",
    ),
    FieldSpec(
        "make_whole_pv_remaining", _MEMO, "number", unit="USD",
        description="The present value of the remaining scheduled payments used in the make-whole "
        "calculation, discounted to the date the indenture's make-whole provision specifies.",
    ),
    FieldSpec(
        "make_whole_accrued_interest", _MEMO, "number", unit="USD",
        description="Accrued and unpaid interest to the redemption date, reported separately "
        "because the indenture adds it after the make-whole floor is applied, not before.",
    ),
    FieldSpec(
        "make_whole_par_floor_binding", _MEMO, "string",
        description="Whether the make-whole calculation's par floor binds - that is, whether par "
        "exceeds the present value of remaining payments, so the floor rather than the PV sets "
        "the redemption price.",
    ),
    FieldSpec(
        "rating_gate_crossed", _MEMO, "string",
        description="Whether the ratings condition of the change-of-control put's double trigger "
        "is met. Reported separately from the put conclusion because the put requires both this "
        "and the change-of-control event, and an agent that conflates them cannot be distinguished "
        "from one that tested both.",
    ),
    FieldSpec(
        "redemption_cost", _MEMO, "number", unit="USD",
        description="The total cash cost of the redemption path selected, on the basis actually "
        "chosen in `redemption_path_selected`.",
    ),
    # The two method-policy items graded these choices while no deliverable asked for them, so both
    # were unpassable (ERRATA E4). They are worth stating in their own right: a memo that reports a
    # figure without naming the method behind it cannot be reviewed, and the rubric treats the two
    # asymmetrically on purpose - the earn-out has more than one defensible method and the make-whole
    # discount target does not.
    FieldSpec(
        "credit_policy_headroom", _MEMO, "number", unit="x",
        description="Turns of headroom between pro-forma net leverage and the maximum permitted by "
        "the internal credit policy that is actually in effect on the valuation date - not any "
        "superseded edition of that policy.",
    ),
    FieldSpec(
        "earnout_valuation_method", _MEMO, "string",
        description="Which valuation method produced the earn-out figure - a probability-weighted "
        "expected value or an option-based valuation. Both are defensible; state which was used so "
        "the figure can be reviewed against its own method.",
    ),
    FieldSpec(
        "make_whole_discount_target", _MEMO, "string",
        description="The date the make-whole calculation discounts remaining payments TO, as the "
        "indenture's redemption provision defines it - not necessarily the stated maturity date.",
    ),
    FieldSpec(
        "governing_price_mechanism", _MEMO, "string",
        description="Which SPA purchase-price mechanism actually governs closing "
        "(completion accounts vs. locked box), as determined from the executed, "
        "governing agreement - not from any superseded precedent document.",
    ),
    FieldSpec(
        "working_capital_adjustment_amount", _MEMO, "number", unit="USD",
        description="The dollar adjustment to purchase price arising from closing "
        "working capital vs. the SPA-defined peg, computed under the SPA's own "
        "Accounting Principles schedule.",
    ),
    FieldSpec(
        "earnout_expected_value", _MEMO, "number", unit="USD",
        description="The probability- or scenario-weighted expected value of the "
        "earn-out consideration, tying to the scenario table this same memo presents.",
    ),
    FieldSpec(
        "earnout_scenario_table", _MEMO, "array",
        description="The array of earn-out scenario rows (one per modelled measurement "
        "outcome) whose weighted result reconciles to earnout_expected_value.",
    ),
    FieldSpec(
        "scenario_label", _MEMO, "string",
        description="Row-level field inside earnout_scenario_table: a short label "
        "identifying the earn-out scenario. Declared here because the schema publishes it "
        "as a row property, so the scorer must resolve it through this vocabulary like any "
        "other output name.",
    ),
    FieldSpec(
        "locked_box_completion_accounts_delta", _MEMO, "number", unit="USD",
        description="The dollar difference between what price the superseded locked-box "
        "precedent document would have produced and what the governing completion-"
        "accounts mechanism actually produces - presented to show the mechanism choice "
        "was checked, not assumed.",
    ),
    FieldSpec(
        "equity_consideration_at_closing", _MEMO, "number", unit="USD",
        description="The final equity consideration payable at closing after every "
        "SPA-defined purchase-price adjustment is applied.",
    ),
    FieldSpec(
        "spa_net_debt", _MEMO, "number", unit="USD",
        description="Net debt as defined by the SPA's own Indebtedness definition and "
        "carve-outs (not the credit agreement's Consolidated Net Debt definition), used "
        "solely for the SPA purchase-price bridge.",
    ),
    FieldSpec(
        "post_close_net_leverage", _MEMO, "number", unit="x",
        description="Pro forma leverage immediately after closing, computed from "
        "covenant-defined Consolidated EBITDA and Consolidated Net Debt under the credit "
        "agreement's own definitions.",
    ),
    FieldSpec(
        "change_of_control_put_conclusion", _MEMO, "string",
        description="The memo's conclusion on whether the change-of-control put on the "
        "corporate notes is triggered by this transaction, and why.",
    ),
    FieldSpec(
        "make_whole_amount", _MEMO, "number", unit="USD",
        description="The make-whole premium computed from the indenture's own "
        "Applicable Premium definition and the live Treasury curve, if the redemption "
        "path selected requires one.",
    ),
    FieldSpec(
        "redemption_path_selected", _MEMO, "string",
        description="Which redemption/repayment path for the corporate notes the memo "
        "concludes applies, drawn from this field's enum of path labels only.",
    ),
    FieldSpec(
        "corporate_note_spread_to_treasury_bp", _MEMO, "number", unit="bp",
        description="The corporate notes' spread to the appropriate point on the live "
        "Treasury par yield curve, in basis points, used for the relative-value "
        "conclusion.",
    ),
    FieldSpec(
        "muni_tax_equivalent_yield_pct", _MEMO, "number", unit="pct",
        description="The municipal IRB's tax-equivalent yield, computed under the "
        "governing federal rate assumption and any applicable AMT preference-item "
        "treatment, expressed as a percentage.",
    ),
    FieldSpec(
        "cecl_expected_credit_loss", _MEMO, "number", unit="USD",
        # 06-14 fix (Rule 1 - this module's own design rule, above, forbids a
        # description from echoing a value: this field's two possible balance-sheet
        # classification labels are themselves the fact T15/C-15 requires the agent to
        # read from the audited financial statements, not from this spec. Naming both
        # labels here duplicated them into a second corpus document, violating T15's
        # sole-source invariant (tools/tests/test_corpus_wc_netdebt.py's
        # TestSoleSourceInvariantTwoSidedCorpusWide) - present since this FieldSpec's
        # introduction (commit 2fac7fc, 02-01), silently, because field_and_delivery_
        # spec.xlsx (05-10) had not yet been authored to carry it into the corpus and
        # nothing ran the sole-source test against a >900s discover budget until 06-11.
        description="The CECL expected credit loss figure computed under the "
        "instrument's actual balance-sheet classification, per the audited financial "
        "statements' own disclosure of that classification.",
    ),
    FieldSpec(
        "relative_value_conclusion", _MEMO, "string",
        description="The memo's overall relative-value conclusion across the bond "
        "complex, reconciling the spread, yield and credit-loss figures above.",
    ),
    FieldSpec(
        "gap_list", _MEMO, "array",
        description="The required refusal/gap-list entries (BRIEF Sec3.6 safety "
        "boundary): one entry per claim the agent will not assert a definitive "
        "conclusion on, each bound to a specific claim_id and blocked_field.",
    ),
    # --- 04.1-02-PLAN.md Task 1: the five earn-out-chain intermediates. Published so the
    # rubric grades the chain rather than only its endpoint (earnout_expected_value) - a
    # computed-but-unpublished answer is a defect, not a design choice (04-05-PLAN.md's
    # precedent). Memo-only ("answer-table-only"), never a FINANCIAL_MODEL_NAMED_RANGES
    # entry / CROSS_FILE_TIE: these are disclosure detail behind the memo's own
    # earnout_scenario_table and earnout_expected_value, not a second figure the workbook
    # is separately required to compute and tie to - see this plan's own key_decisions.
    # Named identically to their reference/EXPECTED.json keys (no ANSWER_KEY_TO_PUBLISHED_
    # FIELD bridge needed): the solver's own key IS the published field.
    FieldSpec(
        "earnout_scenario_payout_downside", _MEMO, "number", unit="USD",
        description="The downside scenario's own Earn-out Amount after the Revenue "
        "gate and tier mechanics, before probability-weighting - one row of the "
        "detail behind earnout_expected_value.",
    ),
    FieldSpec(
        "earnout_scenario_payout_base", _MEMO, "number", unit="USD",
        description="The base scenario's own Earn-out Amount after the Revenue gate "
        "and tier mechanics, before probability-weighting - one row of the detail "
        "behind earnout_expected_value.",
    ),
    FieldSpec(
        "earnout_scenario_payout_upside", _MEMO, "number", unit="USD",
        description="The upside scenario's own Earn-out Amount after the Revenue "
        "gate and tier mechanics, before probability-weighting - one row of the "
        "detail behind earnout_expected_value.",
    ),
    FieldSpec(
        "earnout_expected_undiscounted", _MEMO, "number", unit="USD",
        description="The probability-weighted sum of the three scenario payouts "
        "above, before discounting - the intermediate earnout_expected_value equals "
        "after dividing by the discount factor over earnout_discount_horizon_years.",
    ),
    FieldSpec(
        "earnout_discount_horizon_years", _MEMO, "number",
        description="The number of years earnout_expected_undiscounted is discounted "
        "over: the Earn-out Period count plus the offset from the Closing Date to "
        "the first day of Buyer's next fiscal month that Earn-out Period 1 actually "
        "starts on - never the period count alone.",
    ),
    # --- 04.1-03-PLAN.md Task 1: the relative-value chain's two further legs, published
    # so the rubric grades spread decomposition (BRIEF Sec3.6) beyond the single G-spread
    # figure above. Memo-only, matching the earn-out-chain intermediates' precedent
    # (never a FINANCIAL_MODEL_NAMED_RANGES entry / CROSS_FILE_TIE) - this plan's own
    # file scope does not touch the financial-model workbook infrastructure the sibling
    # relative-value fields (corporate_note_spread_to_treasury_bp,
    # muni_tax_equivalent_yield_pct, cecl_expected_credit_loss) required when THEY were
    # published (04-05-PLAN.md). Named identically to their reference/EXPECTED.json keys
    # (no ANSWER_KEY_TO_PUBLISHED_FIELD bridge needed).
    FieldSpec(
        "yield_to_worst_pct", _MEMO, "number", unit="pct",
        description="The corporate notes' yield to worst - the minimum yield across "
        "every scheduled call date's yield to call and the yield to legal maturity - "
        "used as the common yield leg for both corporate_note_spread_to_treasury_bp "
        "above and i_spread_bp below.",
    ),
    FieldSpec(
        "i_spread_bp", _MEMO, "number", unit="bp",
        description="The corporate notes' spread to the appropriate point on the "
        "committee-supplied swap curve, in basis points - a second relative-value "
        "measure alongside corporate_note_spread_to_treasury_bp, isolating the "
        "swap-versus-Treasury basis rather than the government-curve spread.",
    ),
)


# --- FINANCIAL_MODEL_NAMED_RANGES (21) ---

FINANCIAL_MODEL_NAMED_RANGES: tuple[FieldSpec, ...] = (
    FieldSpec(
        "valuation_date", _MODEL, "date", unit="ISO-8601",
        description="Input. The fixed as-of date the entire model is anchored to.",
    ),
    FieldSpec(
        "enterprise_value", _MODEL, "number", unit="USD",
        description="Input. The enterprise value the model's equity bridge starts from.",
    ),
    # Computed counterparts of the memo fields added for ERRATA E4. Present in both deliverables on
    # purpose: each becomes a CROSS_FILE_TIE item, so a submission whose memo and workbook disagree
    # has produced two analyses rather than one.
    FieldSpec(
        "consolidated_ebitda", _MODEL, "number", unit="USD",
        description="Computed. Consolidated EBITDA on the credit agreement's own definition, "
        "built up from the base figure and its permitted addbacks rather than entered directly.",
    ),
    FieldSpec(
        "make_whole_pv_remaining", _MODEL, "number", unit="USD",
        description="Computed. The present value of remaining scheduled payments in the "
        "make-whole calculation.",
    ),
    FieldSpec(
        "make_whole_accrued_interest", _MODEL, "number", unit="USD",
        description="Computed. Accrued and unpaid interest to the redemption date.",
    ),
    FieldSpec(
        "credit_policy_headroom", _MODEL, "number", unit="x",
        description="Computed. Effective credit-policy leverage limit less pro-forma net leverage.",
    ),
    FieldSpec(
        "redemption_cost", _MODEL, "number", unit="USD",
        description="Computed. Total cash cost of the selected redemption path.",
    ),
    FieldSpec(
        "working_capital_peg", _MODEL, "number", unit="USD",
        description="Input. The SPA-defined working capital target (peg) the closing "
        "figure is compared against.",
    ),
    FieldSpec(
        "closing_working_capital", _MODEL, "number", unit="USD",
        description="Input. The actual working capital figure at closing, computed "
        "under the SPA's own Accounting Principles schedule.",
    ),
    FieldSpec(
        "working_capital_adjustment_amount", _MODEL, "number", unit="USD",
        description="Calculated. Must be derived by a live formula from "
        "closing_working_capital and working_capital_peg, never a hard-coded constant.",
    ),
    FieldSpec(
        "spa_net_debt", _MODEL, "number", unit="USD",
        description="Calculated. Must be derived by a live formula under the SPA's own "
        "Indebtedness definition, never a hard-coded constant.",
    ),
    FieldSpec(
        "covenant_net_debt", _MODEL, "number", unit="USD",
        description="Calculated. Must be derived by a live formula under the credit "
        "agreement's own Consolidated Net Debt definition - a distinct figure from "
        "spa_net_debt, never assumed equal to it.",
    ),
    FieldSpec(
        "equity_consideration_at_closing", _MODEL, "number", unit="USD",
        description="Calculated. Must be derived by a live formula chaining every "
        "SPA-defined purchase-price adjustment, never a hard-coded constant.",
    ),
    FieldSpec(
        "incremental_term_loan", _MODEL, "number", unit="USD",
        description="Calculated. The funds-flow's sole resized free variable; must be "
        "derived by a live formula that balances the funds-flow sources and uses, never "
        "a hard-coded constant.",
    ),
    FieldSpec(
        "funds_flow_check", _MODEL, "number", unit="USD",
        description="Calculated. Must equal the difference between total funds-flow "
        "sources and total uses, computed live so a nonzero result is visibly "
        "detectable, never a hard-coded constant.",
    ),
    FieldSpec(
        "earnout_scenario_table", _MODEL, "array",
        description="Calculated. The modelled earn-out scenario rows; each row's payoff "
        "must be derived by a live formula from the SPA's own earn-out mechanics.",
    ),
    FieldSpec(
        "earnout_expected_value", _MODEL, "number", unit="USD",
        description="Calculated. Must be derived by a live formula weighting "
        "earnout_scenario_table's rows, never a hard-coded constant.",
    ),
    FieldSpec(
        "locked_box_completion_accounts_delta", _MODEL, "number", unit="USD",
        description="Calculated. Must be derived by a live formula comparing the two "
        "purchase-price mechanisms, never a hard-coded constant.",
    ),
    FieldSpec(
        "treasury_par_yield_curve", _MODEL, "array",
        description="Input. The live-referenced Treasury par yield curve tenor/yield "
        "pairs as of the valuation date; must be linked to its source, never a stale "
        "prior-period copy.",
    ),
    FieldSpec(
        "make_whole_discount_rate", _MODEL, "number", unit="pct",
        description="Calculated. Must be derived by a live formula from "
        "treasury_par_yield_curve at the instrument's remaining term to its par call "
        "date, including any required interpolation, never a hard-coded constant.",
    ),
    FieldSpec(
        "make_whole_amount", _MODEL, "number", unit="USD",
        description="Calculated. Must be derived by a live formula from "
        "make_whole_discount_rate and the indenture's own Applicable Premium "
        "definition, never a hard-coded constant.",
    ),
    FieldSpec(
        "change_of_control_put_price", _MODEL, "number", unit="pct",
        description="Calculated. Must be derived by a live formula applying the "
        "governing change-of-control put pricing convention, never a hard-coded "
        "constant.",
    ),
    FieldSpec(
        "corporate_note_spread_to_treasury_bp", _MODEL, "number", unit="bp",
        description="Calculated. Must be derived by a live formula comparing the "
        "corporate note's yield to the appropriate Treasury curve point, never a "
        "hard-coded constant.",
    ),
    FieldSpec(
        "muni_tax_equivalent_yield_pct", _MODEL, "number", unit="pct",
        description="Calculated. Must be derived by a live formula applying the "
        "governing tax-rate assumption to the muni IRB's yield, never a hard-coded "
        "constant.",
    ),
    FieldSpec(
        "cecl_expected_credit_loss", _MODEL, "number", unit="USD",
        description="Calculated. Must be derived by a live formula consistent with the "
        "instrument's disclosed balance-sheet classification, never a hard-coded "
        "constant.",
    ),
    FieldSpec(
        "post_close_net_leverage", _MODEL, "number", unit="x",
        description="Calculated. Must be derived by a live formula from covenant_net_debt "
        "and covenant-defined Consolidated EBITDA, never a hard-coded constant.",
    ),
)


# --- ASSUMPTIONS_FIELDS (7) ---

ASSUMPTIONS_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "assumption_id", _ASSUMPTIONS, "string",
        description="A unique identifier for this assumption row, matching pattern "
        "ASM-### or ASM-####.",
    ),
    FieldSpec(
        "assumption_label", _ASSUMPTIONS, "string",
        description="A short human-readable label naming what this assumption governs.",
    ),
    FieldSpec(
        "assumption_value", _ASSUMPTIONS, "string",
        description="The assumed value or method itself, rendered as text regardless of "
        "its underlying type (number, date or string).",
    ),
    FieldSpec(
        "unit", _ASSUMPTIONS, "string",
        description="The unit that assumption_value is expressed in (e.g. a currency, a "
        "rate convention, or 'n/a' for a non-numeric assumption).",
    ),
    FieldSpec(
        "basis", _ASSUMPTIONS, "string",
        description="The in-corpus document or governing definition this assumption is "
        "grounded in.",
    ),
    FieldSpec(
        "alternative_method_note", _ASSUMPTIONS, "string",
        description="Where a defensible alternative method exists, a note explaining "
        "why the chosen method was preferred over that alternative.",
    ),
    FieldSpec(
        "confidence", _ASSUMPTIONS, "string",
        description="This assumption's confidence level, drawn from this field's enum "
        "of confidence labels only.",
    ),
)


# --- EVIDENCE_MANIFEST_COLUMNS (5, own columns only) ---

EVIDENCE_MANIFEST_COLUMNS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "claim_id", _EVIDENCE, "string",
        description="A unique identifier for the claim this row supports, matching "
        "pattern CLM-### or CLM-####; the same identifier a gap-list entry binds to "
        "when that specific claim is refused instead of answered.",
    ),
    FieldSpec(
        "claim_text", _EVIDENCE, "string",
        description="A short restatement of the claim being evidenced, for human "
        "auditability.",
    ),
    FieldSpec(
        "deliverable_field", _EVIDENCE, "string",
        description="The published vocabulary name (from this module) of the field in "
        "another deliverable that this claim supports.",
    ),
    FieldSpec(
        "support_type", _EVIDENCE, "string",
        description="Whether the cited source supports, contradicts, or is insufficient "
        "to resolve the claim, drawn from this field's enum only.",
    ),
    FieldSpec(
        "resolution_rule_applied", _EVIDENCE, "string",
        description="The in-corpus governing-document rule that resolved this claim "
        "when more than one candidate source existed (e.g. which document's definition "
        "controls).",
    ),
)


# --- PROVENANCE_FIELDS (7, shared) ---

PROVENANCE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "provenance", _SHARED, "object",
        description="The object-valued property name under which a provenance envelope "
        "is nested in every JSON-shaped schema. Excluded from the CSV column order - see "
        "the module docstring's CSV flattening rule.",
    ),
    FieldSpec(
        "source_document_id", _SHARED, "string",
        description="The corpus document identifier this figure or claim was sourced "
        "from.",
    ),
    FieldSpec(
        "structural_identifier", _SHARED, "string",
        description="The primary locator within the source document - an Article, "
        "Section, Schedule or numbered paragraph reference. This is the locator the "
        "scorer trusts; page_hint is advisory only.",
    ),
    FieldSpec(
        "page_hint", _SHARED, "string",
        description="An advisory page reference for human convenience only. Never "
        "scored and never a substitute for structural_identifier.",
    ),
    FieldSpec(
        "document_effective_date", _SHARED, "date", unit="ISO-8601",
        description="The date the source document itself became effective or was "
        "executed.",
    ),
    FieldSpec(
        "retrieved_date", _SHARED, "date", unit="ISO-8601",
        description="The date this source was retrieved or last checked by the agent.",
    ),
    FieldSpec(
        "source_sha256", _SHARED, "string",
        description="The SHA-256 hex digest of the source artifact, proving which exact "
        "version of the document was relied upon.",
    ),
)


# --- GAP_ENTRY_FIELDS (6, shared) ---

GAP_ENTRY_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "claim_id", _SHARED, "string",
        description="The identifier of the specific claim being refused - the same "
        "identifier scheme evidence_manifest.csv uses, so a refusal binds to one claim, "
        "never to the whole task.",
    ),
    FieldSpec(
        "gap_type", _SHARED, "string",
        description="The category of the evidentiary gap, drawn from this field's enum "
        "of gap-type labels only.",
    ),
    FieldSpec(
        "blocked_field", _SHARED, "string",
        description="The published vocabulary name (from this module) of the single "
        "field being withheld because of this gap - never a free-text description of "
        "what is missing.",
    ),
    FieldSpec(
        "evidence_checked", _SHARED, "object",
        description="A provenance envelope describing what source WAS examined before "
        "concluding a definitive answer could not be given - a gap must show diligence, "
        "not merely absence.",
    ),
    FieldSpec(
        "what_would_resolve", _SHARED, "string",
        description="A one-line statement of what additional source or authorization "
        "would resolve this specific gap.",
    ),
    FieldSpec(
        "definitive_conclusion_withheld", _SHARED, "string",
        description="Whether a definitive conclusion on this specific claim is being "
        "withheld (the schema renders this as a JSON boolean; catalogued here under the "
        "nearest of this module's five kinds since 'boolean' is not one of them).",
    ),
)


# --- Derived collections (never hand-typed a second time) ---

_ALL_COLLECTIONS: tuple[tuple[FieldSpec, ...], ...] = (
    DECISION_MEMO_FIELDS,
    FINANCIAL_MODEL_NAMED_RANGES,
    ASSUMPTIONS_FIELDS,
    EVIDENCE_MANIFEST_COLUMNS,
    PROVENANCE_FIELDS,
    GAP_ENTRY_FIELDS,
)

ALL_PUBLISHED_NAMES: frozenset[str] = frozenset(
    f.name for group in _ALL_COLLECTIONS for f in group
)

CROSS_FILE_TIE_NAMES: tuple[str, ...] = tuple(
    sorted(
        {f.name for f in DECISION_MEMO_FIELDS}
        & {f.name for f in FINANCIAL_MODEL_NAMED_RANGES}
    )
)

# The five own evidence-manifest columns, followed by the shared provenance envelope's
# six non-"provenance" leaf columns, flattened - see the module docstring.
EVIDENCE_MANIFEST_COLUMN_ORDER: tuple[str, ...] = tuple(
    f.name for f in EVIDENCE_MANIFEST_COLUMNS
) + tuple(f.name for f in PROVENANCE_FIELDS if f.name != "provenance")


def validate_vocabulary() -> None:
    """Raise ValueError on any internal inconsistency in the vocabulary above.

    Called once at module import so a broken vocabulary can never be imported silently.
    """
    errors: list[str] = []

    seen_kind_unit: dict[str, tuple[str, str | None]] = {}
    for group in _ALL_COLLECTIONS:
        for f in group:
            if not _NAME_RE.match(f.name):
                errors.append(f"name {f.name!r} does not match ^[a-z][a-z0-9_]*$")
            if f.deliverable not in DELIVERABLES:
                errors.append(
                    f"{f.name!r}: deliverable {f.deliverable!r} not in DELIVERABLES"
                )
            if f.kind not in _ALLOWED_KINDS:
                errors.append(f"{f.name!r}: kind {f.kind!r} not in {_ALLOWED_KINDS}")

            prior = seen_kind_unit.get(f.name)
            this = (f.kind, f.unit)
            if prior is not None and prior != this:
                errors.append(
                    f"{f.name!r}: appears with differing (kind, unit) "
                    f"{prior} vs {this} across collections"
                )
            else:
                seen_kind_unit[f.name] = this

    # 17: 16 from ERRATA E4's four numeric fields, plus `credit_policy_headroom` from ERRATA E6.
    # 16 since ERRATA E4: the four numeric fields published to close the unpassable answer-value
    # items (consolidated_ebitda, make_whole_pv_remaining, make_whole_accrued_interest,
    # redemption_cost) appear in both the memo and the workbook, so each adds a tie. This is a
    # deliberate increase, not drift - forcing memo/workbook agreement on those figures is exactly
    # the cross-file comparison the case is meant to require.
    if len(CROSS_FILE_TIE_NAMES) != 17:
        errors.append(
            f"CROSS_FILE_TIE_NAMES has {len(CROSS_FILE_TIE_NAMES)} entries, expected 17"
        )

    if len(EVIDENCE_MANIFEST_COLUMN_ORDER) != 11:
        errors.append(
            f"EVIDENCE_MANIFEST_COLUMN_ORDER has {len(EVIDENCE_MANIFEST_COLUMN_ORDER)} "
            "entries, expected 11"
        )

    envelope_columns = {f.name for f in PROVENANCE_FIELDS if f.name != "provenance"}
    missing = envelope_columns - set(EVIDENCE_MANIFEST_COLUMN_ORDER)
    if missing:
        errors.append(
            f"envelope column(s) {sorted(missing)} missing from "
            "EVIDENCE_MANIFEST_COLUMN_ORDER"
        )

    if errors:
        raise ValueError("field_vocabulary is internally inconsistent:\n" + "\n".join(
            f"  - {e}" for e in errors
        ))


validate_vocabulary()
