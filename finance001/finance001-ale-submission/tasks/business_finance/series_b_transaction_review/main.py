"""ALE cua-bench adapter for the finance001 Series B review task."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shlex
from typing import Any

import cua_bench as cb

from tasks.linux_runtime import LinuxTaskConfig


DOMAIN_NAME = "business_finance"
TASK_NAME = "series_b_transaction_review"
VARIANT_NAME = "base"
TASK_CARD_PATH = Path(__file__).with_name("task_card.json")
SCORE_REPORT_PATH = "/tmp/finance001-score.json"
REQUIRED_ARTIFACTS = (
    "term_sheet_extract.json",
    "capitalization.xlsx",
    "economics.json",
    "deviation_register.csv",
    "negotiation_memo.pdf",
    "redline_positions.csv",
    "ic_recommendation.json",
    "reconciliation.csv",
    "instrument_register.csv",
)


def _task_prompt() -> str:
    card = json.loads(TASK_CARD_PATH.read_text(encoding="utf-8"))
    return str(card["taskPrompt"])


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


def _stderr(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("stderr", ""))
    return str(getattr(result, "stderr", ""))


async def _run_required(session: cb.DesktopSession, command: str, label: str) -> Any:
    result = await session.run_command(command, check=False)
    if _exit_code(result) != 0:
        raise RuntimeError(f"{label} failed: {_stderr(result)[:500]}")
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

    await _run_required(
        session,
        f"rm -rf {output_dir} {reference_dir} && mkdir -p {output_dir}",
        "output preparation",
    )
    await _run_required(
        session,
        f"/usr/bin/python3 {software_dir}/verify_inputs.py {input_dir}",
        "input verification",
    )
    await _run_required(
        session,
        f"test ! -d {reference_dir} || test -z \"$(find {reference_dir} -type f -print -quit)\"",
        "reference isolation check",
    )
    await _run_required(
        session,
        "/usr/local/bin/finance001-services-health",
        "finance001 service health check",
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
    scorer = f"{reference_dir}/case/scripts/score_outputs.py"
    expected = f"{reference_dir}/case/reference/EXPECTED.json"
    command = " ".join(
        (
            "/usr/bin/python3",
            shlex.quote(scorer),
            shlex.quote(output_dir),
            "--expected",
            shlex.quote(expected),
            "--report",
            shlex.quote(SCORE_REPORT_PATH),
        )
    )
    await _run_required(session, command, "private scorer")
    try:
        report = json.loads(await session.read_file(SCORE_REPORT_PATH))
    finally:
        await session.run_command(f"rm -f {shlex.quote(SCORE_REPORT_PATH)}", check=False)
    score = float(report.get("score", 0.0))
    return [score if 0.0 <= score <= 1.0 else 0.0]
