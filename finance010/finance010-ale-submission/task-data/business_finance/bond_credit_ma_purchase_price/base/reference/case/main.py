"""case/main.py - the ALE lifecycle adapter: the single entry point the provider calls.

Three hooks, argparse-dispatched: `python case/main.py load|start|evaluate`.

This file is where ALE's two non-negotiable rules stop being intent and become code.

**The reference answer stays hidden until scoring.** `start()` refuses to run if any private artifact is
reachable in the workspace. The match is case-insensitive, because `expected.json` leaks exactly as much
as `EXPECTED.json` and a case-sensitive check would wave the first one through. It also wipes `output/`
and any reachable `reference/` BEFORE verifying, so a re-used container cannot leak one run's state into
the next - and it wipes first rather than checking first, because a stale `reference/` left by a previous
run is the likeliest way the private layer becomes reachable at all.

**`evaluate()` never raises on bad output.** A missing directory, zero-length files, a CSV with the wrong
columns, a `.pdf` that is not a PDF - each is a score of 0.0, not a traceback. This matters more than it
looks: ALE records an exception as an infrastructure failure, so a scorer that threw on a corrupt
submission would convert "this agent produced unusable output" into "the harness broke", and the run
would be discarded instead of counted as a failure the agent earned. Only a genuine environment-contract
violation - the scorer itself unimportable, the answer table absent - propagates, as
`EnvironmentContractError`.

The asymmetry between `start()` and `evaluate()` is deliberate. `start()` is strict because it guards
integrity and runs against OUR artifacts, where a fault is a build defect that must be loud.
`evaluate()` is forgiving because it runs against an untrusted submission, where a fault is data.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

CASE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = CASE_ROOT.parent
SCRIPTS_DIR = CASE_ROOT / "scripts"
for _path in (str(SCRIPTS_DIR), str(REPO_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

TASK_CARD_PATH = CASE_ROOT / "task_card.json"

# Matched case-insensitively against every path component and file name in the workspace.
PRIVATE_NAMES: tuple[str, ...] = (
    "EXPECTED.json",
    "traps.json",
    "DESIGN-NUMERIC.md",
    "DESIGN-TRAPS.md",
    "solve_reference.py",
    "mk_expected.py",
    "mk_trap_manifest.py",
    "mk_positive_fixture.py",
    "slice_solve.py",
    "SLICE_TIEOUT.json",
    "PHASE4_SLICE_REPORT.json",
    "PHASE1_GATE_REPORT.json",
)

PRIVATE_DIRECTORIES: tuple[str, ...] = ("reference", "fixtures", ".planning", "tools")


class StartupError(RuntimeError):
    """`start()` refused to run. A build/integrity defect, never a submission problem."""


class EnvironmentContractError(RuntimeError):
    """The environment cannot support scoring at all. The ONLY thing `evaluate()` lets out."""


def _deliverables() -> tuple[str, ...]:
    import field_vocabulary as fv

    return tuple(fv.ARTIFACT_DELIVERABLES)


try:
    REQUIRED_ARTIFACTS: tuple[str, ...] = _deliverables()
except Exception:  # noqa: BLE001 - resolved lazily in evaluate() if the import is not ready yet
    REQUIRED_ARTIFACTS = ()


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------


def load() -> dict:
    """Return the task definition as a single JSON-serialisable object."""
    try:
        return json.loads(TASK_CARD_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvironmentContractError(f"cannot read {TASK_CARD_PATH}: {exc}") from None


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


# Markers that identify the AUTHORING repository rather than a run workspace. `_wipe` deletes private
# directories recursively, so pointing it at the repo would delete `tools/`, `reference/` and
# `.planning/` - the entire project, including the only copy of the answer factory.
#
# This is not a theoretical hazard: the first draft of this module defaulted `start()` to the repository
# root, which would have destroyed the project the moment anyone ran `python case/main.py start` from a
# checkout. A destructive default is worth a guard even when the default is later fixed, because the
# next caller to pass a path by mistake gets stopped too.
_AUTHORING_MARKERS: tuple[str, ...] = (".planning", "tools", "DESIGN-NUMERIC.md")


def _looks_like_authoring_repo(root: Path) -> bool:
    return sum(1 for marker in _AUTHORING_MARKERS if (root / marker).exists()) >= 2


def _wipe(workspace: Path) -> list[str]:
    """Remove `output/` and every reachable private directory. Returns what was removed.

    Wiping precedes verification on purpose: a re-used container is the likeliest way a previous run's
    `reference/` becomes reachable, and checking first would fail the run instead of cleaning it.
    """
    if _looks_like_authoring_repo(workspace):
        raise StartupError(
            f"refusing to wipe {workspace}: it looks like the authoring repository, not a run "
            f"workspace (found "
            f"{', '.join(m for m in _AUTHORING_MARKERS if (workspace / m).exists())}). This function "
            f"deletes private directories recursively, which here would destroy the project itself. "
            f"Pass the run workspace explicitly."
        )
    removed: list[str] = []
    output = workspace / "output"
    if output.exists():
        shutil.rmtree(output, ignore_errors=True)
        removed.append("output/")
    output.mkdir(parents=True, exist_ok=True)

    for name in PRIVATE_DIRECTORIES:
        for candidate in workspace.rglob("*"):
            if candidate.is_dir() and candidate.name.casefold() == name.casefold():
                shutil.rmtree(candidate, ignore_errors=True)
                removed.append(f"{candidate.relative_to(workspace).as_posix()}/")
    return removed


def find_private_artifacts(workspace: "str | Path") -> list[str]:
    """Every reachable private artifact, matched case-insensitively.

    Case-insensitive because `expected.json` leaks exactly as much as `EXPECTED.json`, and a rename is
    the cheapest possible evasion of a case-sensitive check.
    """
    root = Path(workspace)
    lowered_names = {n.casefold() for n in PRIVATE_NAMES}
    lowered_dirs = {d.casefold() for d in PRIVATE_DIRECTORIES}
    found: list[str] = []
    if not root.is_dir():
        return found
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            if path.name.casefold() in lowered_dirs:
                found.append(f"{rel}/ (private directory)")
            continue
        if path.name.casefold() in lowered_names:
            found.append(f"{rel} (private artifact)")
        elif any(part.casefold() in lowered_dirs for part in path.relative_to(root).parts[:-1]):
            found.append(f"{rel} (inside a private directory)")
    return found


def start(workspace: "str | Path | None" = None) -> dict:
    """Prepare the workspace for a run, or refuse. Idempotent.

    `workspace` is REQUIRED. There is deliberately no default: this function wipes private directories
    recursively, and the only convenient default - the repository root - is the one path where doing so
    would delete the project. A missing argument is a caller error, not an invitation to guess.
    """
    if workspace is None:
        raise StartupError(
            "start() requires an explicit workspace. It wipes private directories recursively, so "
            "there is no safe default to fall back on."
        )
    root = Path(workspace)
    removed = _wipe(root)

    leaks = find_private_artifacts(root)
    if leaks:
        raise StartupError(
            "private artifacts are reachable in the workspace, so the reference answer would be "
            "available to the agent under test:\n  " + "\n  ".join(leaks)
        )

    input_root = root / "input"
    findings: list[str] = []
    if input_root.is_dir():
        import hash_sidecar

        findings = hash_sidecar.verify_inputs(input_root)
        if findings:
            raise StartupError(
                "input/ does not match its hash sidecar, so the corpus is not the one this case's "
                "answers were derived from:\n  " + "\n  ".join(findings)
            )

    return {
        "status": "ready",
        "workspace": str(root.resolve()),
        "removed": removed,
        "private_artifacts_found": [],
        "input_verified": bool(findings) is False and input_root.is_dir(),
    }


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


def _load_scorer():
    try:
        import score_outputs
    except Exception as exc:  # noqa: BLE001 - an unimportable scorer IS an environment failure
        raise EnvironmentContractError(f"the scorer could not be imported: {exc}") from None
    return score_outputs


def evaluate(workspace: "str | Path | None" = None) -> list[float]:
    """Score the submission. Returns `[0.0]` on any bad output; never raises on submission content.

    The fence is total on purpose. Whatever a submission does - absent, empty, truncated, a `.pdf` that
    is not a PDF, a CSV with the wrong columns - the answer is a score, because ALE reads an exception
    as the harness breaking rather than the agent failing.
    """
    root = Path(workspace) if workspace else REPO_ROOT
    scorer = _load_scorer()  # EnvironmentContractError propagates: this is not submission data

    output_root = root / "output"
    try:
        report = scorer.score(output_root)
        score = float(report.score)
    except Exception:  # noqa: BLE001 - deliberate total fence over submission-derived work
        return [0.0]
    if score != score or score in (float("inf"), float("-inf")):  # NaN/inf guard
        return [0.0]
    return [score]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description="ALE lifecycle adapter for finance010 / T010.")
    parser.add_argument("hook", choices=("load", "start", "evaluate"))
    parser.add_argument("--workspace", default=None)
    args = parser.parse_args(argv)

    if args.hook == "load":
        print(json.dumps(load(), sort_keys=True))
        return 0

    if args.hook == "start":
        try:
            print(json.dumps(start(args.workspace), sort_keys=True))
        except StartupError as exc:
            print(f"STARTUP_REFUSED: {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        print(json.dumps(evaluate(args.workspace)))
    except EnvironmentContractError as exc:
        print(f"ENVIRONMENT_CONTRACT: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
