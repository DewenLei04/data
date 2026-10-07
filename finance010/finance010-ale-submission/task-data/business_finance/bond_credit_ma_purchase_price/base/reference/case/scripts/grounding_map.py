"""Grounding pointers — every rubric item's authority, made explicit.

`SCORE-13`: an item nobody can trace is an item nobody can defend. When a submission disputes a
mark, the only useful answer is "this document, this section" — not "the rubric says so". So each
item carries a pointer to the artifact and division that justifies it, and a release gate proves
every pointer resolves.

The pointers deliberately name a **structural division** (Article, Section, Schedule, Note, a
named range, a manifest column) rather than a page number. Page numbers move when the `.docx`
sources are converted to PDF, and an anchor that moves is an anchor that stops resolving. The
deep-placement requirement is therefore expressed as "in Article X §Y, which falls beyond page
300" rather than "on page 317".
"""

from __future__ import annotations

from typing import NamedTuple


class GroundingPointer(NamedTuple):
    """Where an item's authority lives.

    `artifact` is a corpus path or a frozen private artifact; `division` is the structural
    locator inside it; `why` states in one line what that division establishes.
    """

    artifact: str
    division: str
    why: str


# Frozen private artifacts. These ground the numeric items: the value an item asserts is the value
# the reference solver derived, and the solver's independence is itself gated (REF-02).
EXPECTED_TABLE = "reference/EXPECTED.json"
TRAP_MANIFEST = "reference/traps.json"
NUMERIC_DESIGN = "DESIGN-NUMERIC.md"
TRAP_DESIGN = "DESIGN-TRAPS.md"

# Agent-visible contract artifacts. These ground the structural items.
PROMPT = "00_task/TASK_PROMPT.md"
TASK_CARD = "case/task_card.json"
SCHEMA_DIR = "00_task/output_schemas"


def for_answer_value(half: str, key: str) -> GroundingPointer:
    return GroundingPointer(
        artifact=EXPECTED_TABLE,
        division=f"{half}.{key}",
        why=(
            "the canonical value derived by the reference solver, whose independence from any "
            "authored answer constant is AST-gated"
        ),
    )


def for_trap(trap_id: str, rubric_id: str) -> GroundingPointer:
    return GroundingPointer(
        artifact=TRAP_MANIFEST,
        division=f"{trap_id} -> {rubric_id}",
        why=(
            "the trap row that makes this item discriminating, carrying its in-corpus resolution "
            "rule and its generated cost-if-wrong"
        ),
    )


def for_named_range(range_name: str) -> GroundingPointer:
    return GroundingPointer(
        artifact=f"{SCHEMA_DIR}/financial_model.schema.json",
        division=f"named_ranges.{range_name}",
        why="the published schema declares this named range and whether it must be calculated",
    )


def for_memo_field(field_name: str) -> GroundingPointer:
    return GroundingPointer(
        artifact=f"{SCHEMA_DIR}/decision_memo.schema.json",
        division=f"properties.{field_name}",
        why="the published memo vocabulary; the scorer resolves this name through field_vocabulary",
    )


def for_deliverable(deliverable: str) -> GroundingPointer:
    return GroundingPointer(
        artifact=TASK_CARD,
        division=f"outputFiles[{deliverable}]",
        why="the task card names this deliverable, so its absence is a G0 failure",
    )


def for_tolerance(field_name: str) -> GroundingPointer:
    return GroundingPointer(
        artifact=TASK_CARD,
        division=f"evaluation.tolerances.{field_name}",
        why=(
            "the published tolerance contract, mirrored verbatim into the prompt so no item can "
            "demand a precision the prompt never stated"
        ),
    )


def for_prompt_section(section: str) -> GroundingPointer:
    return GroundingPointer(
        artifact=PROMPT,
        division=section,
        why="the prompt states this requirement, so the item is not an unannounced convention",
    )


def for_method_policy(metric: str) -> GroundingPointer:
    return GroundingPointer(
        artifact=NUMERIC_DESIGN,
        division=f"method policy: {metric}",
        why=(
            "records whether this metric has a genuinely defensible alternative method or exactly "
            "one right answer, so a valid professional choice is not marked wrong"
        ),
    )


def for_evidence_column(column: str) -> GroundingPointer:
    return GroundingPointer(
        artifact=f"{SCHEMA_DIR}/evidence_manifest.schema.json",
        division=f"columns[{column}]",
        why=(
            "the citation contract; the design row requires every material conclusion to cite a "
            "file and section, so a missing column makes claims untraceable"
        ),
    )


def for_gap_field(field_name: str) -> GroundingPointer:
    return GroundingPointer(
        artifact=f"{SCHEMA_DIR}/refusal_gap_list.schema.json",
        division=f"items.properties.{field_name}",
        why=(
            "the claim-bound gap shape; a gap entry carries claim_id so a SPECIFIC claim can be "
            "declined, which is what makes paired refusal grading possible"
        ),
    )


ARTIFACTS_REFERENCED = (
    EXPECTED_TABLE,
    TRAP_MANIFEST,
    NUMERIC_DESIGN,
    PROMPT,
    TASK_CARD,
    f"{SCHEMA_DIR}/decision_memo.schema.json",
    f"{SCHEMA_DIR}/financial_model.schema.json",
    f"{SCHEMA_DIR}/assumptions_and_sources.schema.json",
    f"{SCHEMA_DIR}/evidence_manifest.schema.json",
    f"{SCHEMA_DIR}/provenance_envelope.schema.json",
    f"{SCHEMA_DIR}/refusal_gap_list.schema.json",
)
