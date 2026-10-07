"""The rubric registry — exactly 175 items, exactly 60 core, fanned out from frozen structure.

Nothing here is hand-enumerated. Every item is generated from a structure that already exists and
is already gated: the answer table's keys, the published field vocabulary, the trap manifest's
`rubric_items_driven`, and the tolerance contract. That matters for two reasons.

**The counts cannot drift silently.** If a named range is added to the schema, the fan-out produces
one more `RANGE_USED` and one more `RANGE_CALCULATED` item and the count assertion fails loudly at
151 rather than the rubric quietly measuring something different from what it claims. A hand-typed
list of 174 items would have absorbed the change without a word.

The fan-out has a matching hazard, though, and it bit once: generating from the wrong structure
produces a confidently-wrong count. The G0 family was fanned out over `DELIVERABLES`, whose last
member is a sentinel rather than a file, so the rubric asserted the presence of an artifact no agent
could produce — and because that item sat in the G0 gate, every submission scored 0.0. The count
assertion could not catch it: 150 was exactly what the buggy derivation produced, so the number
agreed with itself. Counts detect *drift*; they do not detect a derivation that was wrong from the
start. Only a test that scored a well-formed submission and expected a non-zero result would have,
and that test did not exist. It does now (`test_gates.py::TestG0PositivePath`).

**Every item resolves through the published vocabulary.** Schema/scorer name drift caused the
majority of the reference case's blind-trial score losses, so no item may name a field the
vocabulary does not publish.

## Why an item is core

An item is core when **failing it means the analysis is wrong**, not merely incompletely presented.
Four families qualify, and the 54 falls out of them rather than being chosen to hit a number:

| Family | Count | Why failing it means "wrong" |
|---|---|---|
| `ANSWER_VALUE` | 26 | a wrong canonical figure is a wrong analysis |
| `TRAP_DRIVEN` | 25 | falling into a registered trap is being wrong for a known reason |
| `G0_PARSEABLE` | 4 | an unparseable deliverable means nothing can be assessed at all |
| `EVIDENCE_COLUMN` | 5 | without the citation contract no conclusion is traceable, which the design row makes mandatory |

Presentation families — memo fields, assumption fields, provenance fields, gap shape — are scored
but **not** core: a correct analysis reported untidily is still a correct analysis, and making
tidiness core would let formatting failures masquerade as analytical ones.

## The eight failure modes these item shapes prevent

Each `Check` records the assertion *kind* so the shape is inspectable rather than trusted:
`VALUE` asserts a figure against the tolerance contract, never mere presence, and never
"differs from the stale value" — that would reward changing a wrong number to another wrong number.
`CALCULATED` fails a hard-coded cell even when its value is right, because the workbook was
required to compute it. `EXCLUSIVE` fails a submission that states two contradictory answers, so
hedging both ways cannot win. `TRACEABLE` requires a citation, so a paraphrase cannot satisfy it.
No item rewards length, and no insight is graded twice — the trap-driven family is keyed on the
manifest's own `rubric_items_driven`, which is one row per trap.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from case.scripts import field_vocabulary as fv
from case.scripts import grounding_map as gm

REPO = Path(__file__).resolve().parents[2]

# 179 / 59. Five corrections moved this off the originally-frozen 150 / 55, both recorded in
# DESIGN-NUMERIC.md. ERRATA E3 removed an unpassable G0 item that asserted a sentinel as a file
# (150 -> 149). ERRATA E4 published seven answer values the rubric graded but no deliverable
# carried, which added 19 items across MEMO_FIELD, RANGE_USED, RANGE_CALCULATED and CROSS_FILE_TIE
# (149 -> 168), and dropped the duplicated `coupled.outer_iterations` from a graded half into `meta`
# only, since an iteration count is a fact about this solver rather than about the analysis
# (168 -> 167). All were defect corrections: before them the scorer could not return a non-zero
# score for any submission whatsoever.
#
# Superseded note (E3): The G0 family was fanned out over `DELIVERABLES`,
# whose last member is the `"shared"` sentinel rather than a file, so the rubric carried a presence
# check on an artifact that could never exist. Removing it is a defect correction, not a relaxation:
# the item it deletes was unpassable, and while it stood, G0 failed for every submission and the
# scorer returned 0.0 regardless of quality. ERRATA E3 in DESIGN-NUMERIC.md records the change.
#
# 167 -> 174 is not accounted for by an ERRATA entry in this module's own comment history above -
# ERRATA E6 (DESIGN-NUMERIC.md's errata log) published `credit_policy_headroom` as a new memo field
# AND named range (+2 MEMO_FIELD/RANGE_USED/RANGE_CALCULATED-family items each is one factor; the
# exact reconciliation to 174 predates this plan and was not re-derived here - out of this plan's
# scope to audit).
#
# 174 -> 179 (04-05-PLAN.md Task 1, this plan): five previously-uncomputed canonical answers were
# published in `reference/EXPECTED.json` - `bond.corporate_note_spread_to_treasury_bp`,
# `bond.cecl_expected_credit_loss`, `bond.make_whole_discount_rate`,
# `spa.locked_box_completion_accounts_delta`, `coupled.covenant_net_debt` - each a NEW key in an
# already-graded half, so `build_registry()`'s `ANSWER_VALUE` fan-out (`for key in
# sorted(expected[half])`) mechanically grows by exactly one item per new key: 20 -> 25 ANSWER_VALUE
# items, 174 -> 179 total, 54 -> 59 core (ANSWER_VALUE is a core family). This is the intended,
# designed consequence of publishing a gap, not drift: three of the five (spread, CECL,
# locked-box delta) ALSO already had a pre-existing `CROSS_FILE_TIE` item (unaffected in count,
# since CROSS_FILE_TIE fans out over `field_vocabulary`, not `EXPECTED.json`) that previously fell
# through to presence-only grading and now grades against a real value - see this plan's Task 2. A
# sixth figure, `muni_tax_equivalent_yield_pct`, was computed but was NOT published in 04-05 - a
# leak-gate collision with a legitimate corpus input fact, found by `tools/probe_leak_check.py` -
# and its pre-existing `CROSS_FILE_TIE` item stayed presence-graded via `PRESENCE_BY_DESIGN`.
#
# 179 -> 180 (04-06-PLAN.md Task 0a): that decision is reversed. `muni_tax_equivalent_yield_pct`
# is now published in `reference/EXPECTED.json`'s `bond` half - value-grading is strictly better
# than presence-grading here (it lets through nothing extra and catches the naive gross-up that
# ignores the AMT/CAMT wrinkle, 56 tolerance-widths off), and the leak-gate collision is handled
# by a narrow, exact-value exemption in `tools/probe_leak_check.py`
# (`_muni_tey_collapse_exempt_value()`) rather than by withholding the answer. `build_registry()`'s
# `ANSWER_VALUE` fan-out mechanically grows by exactly one item: 25 -> 26 ANSWER_VALUE items,
# 179 -> 180 total, 59 -> 60 core. The pre-existing `CROSS_FILE_TIE` item for this field is
# UNCHANGED in count (fans out over `field_vocabulary`, not `EXPECTED.json`) but now grades
# against the real value instead of falling back to `PRESENCE_BY_DESIGN`.
#
# 180 -> 175 (04-06-PLAN.md Task 1, same plan). Wiring `KIND_CALCULATED` to a real formula-
# presence check (`xlsx_raw.named_range_is_calculated`) for the first time exposed that
# `RANGE_CALCULATED` was fanned out over ALL 26 `FINANCIAL_MODEL_NAMED_RANGES`, including the five
# `field_vocabulary.py` itself documents as "Input." (`valuation_date`, `enterprise_value`,
# `working_capital_peg`, `closing_working_capital`, `treasury_par_yield_curve`) - never "Must be
# derived by a live formula" like every other name in that tuple. A RANGE_CALCULATED item asking
# whether a declared INPUT was computed has no correct answer other than fail, for every
# submission, forever - the identical "unpassable regardless of quality" defect class ERRATA
# E3/E4 record, just never surfaced before because the fall-through this plan replaces graded it
# on presence, which every one of those five happily satisfies. See build_registry()'s own comment
# at the RANGE_CALCULATED fan-out for the full derivation, including why RANGE_USED keeps all 26
# (existence-and-reference is a meaningful question for an input too). This is a defect
# correction, not a relaxation, exactly as E3 was for the unpassable G0 sentinel item: the
# corrected number is HARDER to reach 100% of, since it removes five items that were free passes
# under the old fall-through, never five items that were previously worth something real.
# RANGE_CALCULATED 26 -> 21, 180 -> 175 total, core unaffected (RANGE_CALCULATED is not a core
# family) at 60.
#
# 175 -> 188 (04.1-02-PLAN.md). Five previously-computed-but-discarded earn-out-chain
# intermediates (`spa.earnout_scenario_payout_downside/base/upside`,
# `spa.earnout_expected_undiscounted`, `spa.earnout_discount_horizon_years`) are published in
# `reference/EXPECTED.json`, and three new trap rows (T26 Jensen's-inequality omission, T27 the
# flat-catch-up-rate misreading, T28 the discount horizon computed without the fiscal-month
# offset) are appended to `tools/traps.py` after T25, each bound to one of those five new keys.
# `ANSWER_VALUE` grows 26 -> 31 and `TRAP_DRIVEN` grows 25 -> 28 mechanically (`build_registry()`
# fans both out directly from `EXPECTED.json`'s own keys / the manifest's own
# `rubric_items_driven`): 175 -> 180 -> 183, 60 -> 65 -> 68 core.
#
# 183 -> 188 (same plan, a THIRD mechanical consequence this plan's own draft arithmetic did not
# anticipate). `tools.check_derivable`'s D1 REACHABLE check requires every graded answer key to
# resolve to a field the agent is actually asked to report - an answer with no agent-visible home
# is unpassable, and being core, pins every score at 0.0 (the identical ERRATA E3/E4 defect class).
# Closing that requirement means each of the five new keys is ALSO a new
# `field_vocabulary.DECISION_MEMO_FIELDS` entry (memo-only, deliberately never a
# `FINANCIAL_MODEL_NAMED_RANGES` entry / `CROSS_FILE_TIE` - see `field_vocabulary.py`'s own
# comment at the fan-out), and `MEMO_FIELD` fans out over ALL of `DECISION_MEMO_FIELDS`
# mechanically, the same way `ANSWER_VALUE` fans out over all of `EXPECTED.json`'s keys. Five new
# memo fields therefore add five new `MEMO_FIELD` items on top of the five `ANSWER_VALUE` items -
# a second rubric item per new answer, from the SAME action, not a second, hand-added one.
# `MEMO_FIELD` is a presentation family (not core), so core is unaffected by this step:
# 183 -> 188 total, 68 core unchanged. Final: **175 -> 188 total, 60 -> 68 core.**
# `tools/check_no_scorer_purchase.py`'s own growth-explanation formula was extended in the same
# plan to credit this second, legitimate mechanical path (previously it credited only new
# `ANSWER_VALUE`/`TRAP_DRIVEN` items) - see that module's own comment at the fix.
#
# 188 -> 194 (04.1-03-PLAN.md). Two previously-computed-but-discarded relative-value figures are
# published in `reference/EXPECTED.json`'s `bond` half - `yield_to_worst_pct` (the SAME Decimal
# the pre-existing G-spread computation already selects) and `i_spread_bp` (the swap-curve leg of
# spread decomposition, BRIEF Sec3.6) - and two new trap rows are appended to `tools/traps.py`
# after T28: T30 (the naive marginal-rate gross-up of the municipal IRB's yield, registering an
# EXISTING wrong reading of the already-published `muni_tax_equivalent_yield_pct` - that value is
# unchanged) and T31 (a yield-to-worst search that never tests legal maturity as a candidate
# redemption, on the newly-published `yield_to_worst_pct`). A third planned figure,
# `bond.z_spread_bp`, and its trap T29 were DROPPED before either reached this registry:
# `tools/solve_reference.py` has no method that bootstraps a spot curve from the published par
# curve (only one that interpolates a spot curve already given), and that module is out of
# 04.1-03-PLAN.md's file scope - adding a bootstrap would be a spine change, not hardening, per
# that plan's own key_decisions. `ANSWER_VALUE` grows 31 -> 33 and `TRAP_DRIVEN` grows 28 -> 30
# mechanically (`build_registry()` fans both out directly from `EXPECTED.json`'s own keys / the
# manifest's own `rubric_items_driven`): 188 -> 190 -> 192, 68 -> 70 -> 72 core. The identical
# third mechanical consequence 04.1-02 found recurs here: `tools.check_derivable`'s D1 REACHABLE
# check requires each new answer key to resolve to a field the agent is actually asked to report,
# so both new keys are also new `field_vocabulary.DECISION_MEMO_FIELDS` entries (memo-only, never
# a `FINANCIAL_MODEL_NAMED_RANGES`/`CROSS_FILE_TIE` - matching the earn-out-chain intermediates'
# precedent, since this plan's own file scope does not touch the financial-model workbook
# infrastructure the sibling relative-value fields required when THEY were published). `MEMO_
# FIELD` fans out over ALL of `DECISION_MEMO_FIELDS` mechanically, adding two more (presentation
# family, not core): 192 -> 194 total, 72 core unchanged. Final: **188 -> 194 total, 68 -> 72
# core.**
TOTAL_ITEMS = 194
CORE_ITEMS = 72

# Halves of the answer table that carry gradeable canonical values. `meta` is diagnostic - it
# records how the solve behaved (iteration count, regime, which CECL subtopic applied) rather than
# an answer the submission is asked to produce, so grading it would penalise the agent for not
# reporting our internals.
ANSWER_HALVES = ("bond", "spa", "coupled")

# Assertion kinds. The kind is stored so a reviewer can see an item asserts a VALUE rather than
# mere presence without reading its implementation.
KIND_VALUE = "VALUE"
KIND_CALCULATED = "CALCULATED"
KIND_REFERENCED = "REFERENCED"
KIND_PARSEABLE = "PARSEABLE"
KIND_TRACEABLE = "TRACEABLE"
KIND_EXCLUSIVE = "EXCLUSIVE"
KIND_SHAPE = "SHAPE"
KIND_POLICY = "POLICY"

GATE_G0 = "G0"
GATE_G1 = "G1"
GATE_G2 = "G2"

# Families whose failure means the analysis is wrong. See the module docstring.
CORE_FAMILIES = frozenset({"ANSWER_VALUE", "TRAP_DRIVEN", "G0_PARSEABLE", "EVIDENCE_COLUMN"})


class Check(NamedTuple):
    id: str
    family: str
    kind: str
    gate: str
    target: str
    grounding: gm.GroundingPointer
    starred: bool


def _expected() -> dict:
    return json.loads((REPO / "reference" / "EXPECTED.json").read_text(encoding="utf-8"))


def _trap_rubric_ids() -> tuple[str, ...]:
    rows = json.loads((REPO / "reference" / "traps.json").read_text(encoding="utf-8"))
    entries = rows.get("traps") if isinstance(rows, dict) else rows
    found: set[str] = set()
    for row in entries or ():
        for rubric_id in row.get("rubric_items_driven") or ():
            found.add(rubric_id)
    return tuple(sorted(found))


def _names(collection) -> tuple[str, ...]:
    return tuple(getattr(spec, "name", spec) for spec in collection)


def build_registry() -> tuple[Check, ...]:
    """Fan out every family from frozen structure. No item is hand-enumerated."""
    expected = _expected()
    checks: list[Check] = []

    def add(family: str, kind: str, gate: str, target: str, grounding: gm.GroundingPointer) -> None:
        checks.append(
            Check(
                id=f"R-{family}-{sum(1 for c in checks if c.family == family) + 1:02d}",
                family=family,
                kind=kind,
                gate=gate,
                target=target,
                grounding=grounding,
                starred=family in CORE_FAMILIES,
            )
        )

    # G0 - every named deliverable is present and parseable.
    #
    # `ARTIFACT_DELIVERABLES`, never `DELIVERABLES`: the latter's last member is the "shared"
    # sentinel marking fields that live in the shared provenance/gap envelope, not a file. Iterating
    # it here put a presence check on a file no agent can produce, so G0 failed for every
    # submission and every score was 0.0 - see ERRATA E3 in DESIGN-NUMERIC.md.
    for deliverable in _names(fv.ARTIFACT_DELIVERABLES):
        add("G0_PARSEABLE", KIND_PARSEABLE, GATE_G0, deliverable, gm.for_deliverable(deliverable))

    # Canonical values. One item per gradeable answer key, asserted against the tolerance contract.
    for half in ANSWER_HALVES:
        for key in sorted(expected[half]):
            add("ANSWER_VALUE", KIND_VALUE, GATE_G2, f"{half}.{key}", gm.for_answer_value(half, key))

    # Named ranges: declared AND actually resolving to a real workbook reference, then
    # calculated rather than hard-coded.
    #
    # RANGE_CALCULATED is fanned out over every name EXCEPT the ones `field_vocabulary.py` itself
    # declares "Input." (`valuation_date`, `enterprise_value`, `working_capital_peg`,
    # `closing_working_capital`, `treasury_par_yield_curve`) - found while wiring KIND_CALCULATED
    # to a REAL formula-presence check for the first time (04-06-PLAN.md Task 1). Before this
    # wiring, RANGE_CALCULATED fell through to a bare presence check, so covering all 26 names
    # cost nothing; a real check applied to a name the vocabulary itself documents as an INPUT
    # ("Input. The fixed as-of date the entire model is anchored to" - never "Must be derived by a
    # live formula") has no correct answer other than fail, for every submission, forever - the
    # exact "scorer could not return a non-zero score for any submission whatsoever" defect class
    # ERRATA E3/E4 exist to name. `tools/submission_fixture.py`'s own positive-fixture builder
    # already drew this identical Input/Calculated line (`spec.description.startswith("Input.")`)
    # to decide value= vs formula= per cell - this fan-out now reads the SAME distinction rather
    # than contradicting it. RANGE_USED is UNCHANGED in FAN-OUT (still all 26 names): whether a
    # declared name resolves to a real workbook reference rather than merely existing is a
    # meaningful, passable question for an input too (a model that declares `enterprise_value`
    # but whose name is dangling or a bare literal constant is still a real defect). Its
    # ENFORCEMENT changed in 04-09-PLAN.md: `xlsx_raw.named_range_resolves` no longer requires
    # some OTHER cell's formula to cite the name by identifier - `financial_model.schema.json`'s
    # `must_reference_range` never asked for that, and requiring it was failing 12/26 of a real
    # submission's honestly-declared, address-referencing named ranges.
    for range_name in _names(fv.FINANCIAL_MODEL_NAMED_RANGES):
        add("RANGE_USED", KIND_REFERENCED, GATE_G1, range_name, gm.for_named_range(range_name))
    for spec in fv.FINANCIAL_MODEL_NAMED_RANGES:
        if spec.description.startswith("Input."):
            continue
        add("RANGE_CALCULATED", KIND_CALCULATED, GATE_G1, spec.name, gm.for_named_range(spec.name))

    # Cross-file agreement on the figures that appear in more than one deliverable.
    for tie in _names(fv.CROSS_FILE_TIE_NAMES):
        add("CROSS_FILE_TIE", KIND_VALUE, GATE_G1, tie, gm.for_tolerance(tie))

    # Presentation families - scored, deliberately not core.
    for field in _names(fv.DECISION_MEMO_FIELDS):
        add("MEMO_FIELD", KIND_EXCLUSIVE, GATE_G1, field, gm.for_memo_field(field))
    for field in _names(fv.ASSUMPTIONS_FIELDS):
        add("ASSUMPTION_FIELD", KIND_TRACEABLE, GATE_G1, field, gm.for_prompt_section("Assumptions"))
    for column in _names(fv.EVIDENCE_MANIFEST_COLUMNS):
        add("EVIDENCE_COLUMN", KIND_TRACEABLE, GATE_G1, column, gm.for_evidence_column(column))
    for field in _names(fv.PROVENANCE_FIELDS):
        add("PROVENANCE_FIELD", KIND_SHAPE, GATE_G1, field, gm.for_prompt_section("Provenance"))

    # One item per registered trap, keyed on the manifest's own rubric_items_driven, so a single
    # insight is never graded twice.
    for rubric_id in _trap_rubric_ids():
        add("TRAP_DRIVEN", KIND_VALUE, GATE_G2, rubric_id, gm.for_trap(rubric_id, rubric_id))

    # The claim-bound gap shape that makes paired refusal grading possible.
    for field in _names(fv.GAP_ENTRY_FIELDS):
        add("GAP_SHAPE", KIND_SHAPE, GATE_G1, field, gm.for_gap_field(field))

    # Method policy: exactly the two classes - a metric with a genuinely defensible alternative,
    # and one with a single right answer. Accepting the first prevents marking a valid
    # professional choice wrong; pinning the second prevents accepting a wrong one.
    add("METHOD_POLICY", KIND_POLICY, GATE_G1, "earnout_valuation_method",
        gm.for_method_policy("earn-out valuation: probability-weighted or option-based"))
    add("METHOD_POLICY", KIND_POLICY, GATE_G1, "make_whole_discount_target",
        gm.for_method_policy("make-whole discounting: to the par call date, single right answer"))

    return tuple(checks)


CHECKS: tuple[Check, ...] = build_registry()


def counts() -> tuple[int, int]:
    return len(CHECKS), sum(1 for c in CHECKS if c.starred)


def assert_counts() -> None:
    """Fail loudly on drift. 149 or 151 means the rubric no longer measures what it claims."""
    total, core = counts()
    if total != TOTAL_ITEMS or core != CORE_ITEMS:
        by_family: dict[str, int] = {}
        for check in CHECKS:
            by_family[check.family] = by_family.get(check.family, 0) + 1
        raise AssertionError(
            f"rubric registry produced {total} items ({core} core); the contract is "
            f"{TOTAL_ITEMS}/{CORE_ITEMS}. Family counts: {sorted(by_family.items())}"
        )


assert_counts()


def _main() -> int:
    import sys

    if "--counts" in sys.argv:
        total, core = counts()
        print(total, core)
        return 0

    by_family: dict[str, list[Check]] = {}
    for check in CHECKS:
        by_family.setdefault(check.family, []).append(check)

    print("Rubric registry")
    print("=" * 72)
    for family in sorted(by_family):
        items = by_family[family]
        marker = "core" if items[0].starred else "    "
        print(f"  {marker}  {family:<20} {len(items):>3}  gate {items[0].gate}  kind {items[0].kind}")
    total, core = counts()
    print("-" * 72)
    print(f"  {total} items, {core} core  (contract {TOTAL_ITEMS}/{CORE_ITEMS})")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
