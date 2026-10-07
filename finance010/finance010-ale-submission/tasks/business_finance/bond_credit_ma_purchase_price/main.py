"""ALE cua-bench adapter for the finance010 bond-credit / purchase-price task.

This is the HARNESS-side entry point ALE calls. It is not the same file as the case's own
`case/main.py`, which is the in-workspace lifecycle adapter and ships inside the private
reference layer; this one drives that one over a `DesktopSession`.

Two things here are finance010-specific and worth reading before changing them.

**The private layer is reassembled before scoring, not scored in place.** `case/scripts/`
resolves the answer table, the trap manifest, the published output schemas and the input
corpus by walking up from its own location:

    rubric_registry.py    REPO/reference/EXPECTED.json, REPO/reference/traps.json
    submission_reader.py  REPO/00_task/output_schemas/*.schema.json
    score_outputs.py      REPO/input/**

where ``REPO`` is two levels above `case/scripts/`. The ALE package cannot ship that shape
directly: `task-data/<task>/base/` is fixed to exactly `input`, `reference` and `software`, so
the private tree lands one level deeper than the scorer expects. `evaluate` therefore builds
the shape the scorer was written against under a scratch root, copying the staged corpus in as
`input/`. Nothing is moved and the agent-visible tree is never written to.

**`start` deletes the reference directory before the agent runs.** In this case's staging mode
(`local:task-data`) the answer key is copied into the sandbox only after the agent exits, so
under normal operation there is nothing there to delete. The `rm -rf` is for the abnormal
path -- a re-used container, an operator staging out of order -- and the emptiness assertion
that follows it is what actually proves the guarantee held. Deleting before asserting, rather
than asserting first, means a stale reference from a previous run is cleaned instead of
turning into a failed run.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shlex
from typing import Any

import cua_bench as cb

from tasks.linux_runtime import LinuxTaskConfig


DOMAIN_NAME = "business_finance"
TASK_NAME = "bond_credit_ma_purchase_price"
VARIANT_NAME = "base"
TASK_CARD_PATH = Path(__file__).with_name("task_card.json")

# Scratch root the private evaluator tree is reassembled under. Outside the agent's workspace
# on purpose: `base/` is the agent's, and the scorer must not be able to observe or be
# perturbed by anything the agent left behind outside `output/`.
PRIVATE_ROOT = "/tmp/finance010-private"

REQUIRED_ARTIFACTS = (
    "decision_memo.pdf",
    "financial_model.xlsx",
    "assumptions_and_sources.xlsx",
    "evidence_manifest.csv",
)

# `case/main.py evaluate` exits 2, and only 2, when the environment cannot support scoring at
# all -- an unimportable scorer, a missing answer table. That is an infrastructure failure and
# must propagate. Every submission-side problem is a score of 0.0 with exit 0 by design.
ENVIRONMENT_CONTRACT_EXIT = 2


def _card() -> dict:
    return json.loads(TASK_CARD_PATH.read_text(encoding="utf-8"))


def _task_prompt() -> str:
    return str(_card()["taskPrompt"])


def _expected_corpus_digest() -> str:
    """The aggregate corpus digest `software/verify_inputs.py` is checked against.

    Derived from this card's own `inputInventory`, which the packager wrote from the real corpus
    at build time and `verify_submission()` recomputes against the packaged `base/input` tree on
    every `--verify`. So there is no constant anybody maintains: edit a corpus byte and the
    build fails before the sandbox is reached; alter one during staging and `start` refuses.

    The recipe is duplicated -- deliberately, and only twice -- in the shipped
    `software/verify_inputs.py`'s `digest_from_inventory`, which carries the reasoning for the
    exact byte layout. The two must agree, and the authoring repository has a test that proves
    they do against the real corpus rather than leaving it to inspection.
    """
    inventory = _card()["inputInventory"]
    digest = hashlib.sha256()
    for entry in sorted(inventory, key=lambda item: str(item["path"])):
        digest.update(str(entry["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _exit_code(result: Any) -> int:
    if isinstance(result, dict):
        return int(
            result.get(
                "return_code",
                result.get("exit_code", result.get("returncode", 0)),
            )
        )
    return int(
        getattr(
            result,
            "return_code",
            getattr(result, "exit_code", getattr(result, "returncode", 0)),
        )
    )


def _stream(result: Any, name: str) -> str:
    if isinstance(result, dict):
        return str(result.get(name, ""))
    return str(getattr(result, name, ""))


async def _run_required(session: cb.DesktopSession, command: str, label: str) -> Any:
    result = await session.run_command(command, check=False)
    if _exit_code(result) != 0:
        raise RuntimeError(f"{label} failed: {_stream(result, 'stderr')[:500]}")
    return result


@dataclass
class TaskConfig(LinuxTaskConfig):
    DOMAIN_NAME: str = DOMAIN_NAME
    TASK_NAME: str = TASK_NAME
    VARIANT_NAME: str = VARIANT_NAME

    @property
    def task_description(self) -> str:
        return (
            f"Authoritative input directory: {self.input_dir}\n"
            f"Required output directory: {self.remote_output_dir}\n\n"
            + _task_prompt()
        )

    def to_metadata(self) -> dict:
        metadata = super().to_metadata()
        metadata["output_files"] = list(REQUIRED_ARTIFACTS)
        return metadata


@cb.tasks_config(split="train")
def load():
    cfg = TaskConfig()
    return [
        cb.Task(
            description=cfg.task_description,
            metadata=cfg.to_metadata(),
            computer={
                "provider": "computer",
                "setup_config": {"os_type": cfg.OS_TYPE},
            },
        )
    ]


@cb.setup_task(split="train")
async def start(task_cfg, session: cb.DesktopSession):
    metadata = task_cfg.metadata
    output_dir = shlex.quote(metadata["remote_output_dir"])
    input_dir = shlex.quote(metadata["input_dir"])
    software_dir = shlex.quote(metadata["software_dir"])
    reference_dir = shlex.quote(metadata["reference_dir"])
    private_root = shlex.quote(PRIVATE_ROOT)

    await _run_required(
        session,
        f"rm -rf {output_dir} {reference_dir} {private_root} && mkdir -p {output_dir}",
        "output preparation",
    )
    await _run_required(
        session,
        f"/usr/bin/python3 {software_dir}/verify_inputs.py {input_dir} "
        f"--expect-corpus-digest {shlex.quote(_expected_corpus_digest())}",
        "input verification",
    )
    await _run_required(
        session,
        f"test ! -d {reference_dir} || test -z \"$(find {reference_dir} -type f -print -quit)\"",
        "reference isolation check",
    )
    # The whole corpus and all four deliverables are specified in terms of four font families.
    # If fontconfig resolves them outside the metric-compatible Liberation/Carlito set the
    # sandbox was built with, page geometry shifts and the agent's own PDF export stops
    # matching the documents it is reading. Cheaper to fail here than to score the result.
    await _run_required(
        session,
        "/usr/local/bin/finance010-runtime-health",
        "finance010 runtime health check",
    )


@cb.evaluate_task(split="train")
async def evaluate(task_cfg, session: cb.DesktopSession) -> list[float]:
    metadata = task_cfg.metadata
    output_dir = metadata["remote_output_dir"]
    presence_command = " && ".join(
        f"test -f {shlex.quote(f'{output_dir}/{name}')}" for name in REQUIRED_ARTIFACTS
    )
    presence = await session.run_command(presence_command, check=False)
    if _exit_code(presence) != 0:
        return [0.0]

    reference_dir = metadata["reference_dir"]
    private_root = shlex.quote(PRIVATE_ROOT)
    quoted_reference = shlex.quote(reference_dir)
    quoted_input = shlex.quote(metadata["input_dir"])
    workspace = shlex.quote(str(Path(output_dir).parent))

    # Reassemble the layout case/scripts/ resolves its paths against: case/, reference/,
    # 00_task/ from the staged private tree, plus a copy of the corpus as input/.
    await _run_required(
        session,
        " && ".join(
            (
                f"rm -rf {private_root}",
                f"mkdir -p {private_root}",
                f"cp -a {quoted_reference}/case {quoted_reference}/reference "
                f"{quoted_reference}/00_task {private_root}/",
                f"mkdir -p {private_root}/input",
                f"cp -a {quoted_input}/. {private_root}/input/",
            )
        ),
        "private evaluator staging",
    )

    command = " ".join(
        (
            "/usr/bin/python3",
            f"{private_root}/case/main.py",
            "evaluate",
            "--workspace",
            workspace,
        )
    )
    try:
        result = await session.run_command(command, check=False)
        code = _exit_code(result)
        if code == ENVIRONMENT_CONTRACT_EXIT:
            raise RuntimeError(
                "finance010 scorer reported an environment contract failure: "
                + _stream(result, "stderr")[:500]
            )
        if code != 0:
            raise RuntimeError(f"private scorer failed: {_stream(result, 'stderr')[:500]}")
        payload = _stream(result, "stdout").strip()
    finally:
        await session.run_command(f"rm -rf {private_root}", check=False)

    # `case/main.py evaluate` prints exactly `[<score>]`. A stdout that will not parse means
    # the scorer did not run as contracted, which is an environment failure, not a bad
    # submission -- a bad submission already came back as [0.0] from inside that process.
    try:
        scores = json.loads(payload.splitlines()[-1])
        score = float(scores[0])
    except (ValueError, IndexError, TypeError) as exc:
        raise RuntimeError(
            f"private scorer produced unparseable output ({type(exc).__name__}): {payload[:200]!r}"
        ) from None
    return [score if 0.0 <= score <= 1.0 else 0.0]
