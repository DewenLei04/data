"""AgentHLE task: legal/us_crfa_preenactment_review_01."""

from __future__ import annotations

import json
import logging
import os
import sys
import types
from dataclasses import dataclass

import cua_bench as cb

from tasks.common_setup import BaseTaskSetup
from tasks.legal.us_crfa_preenactment_review_01.scripts.score_outputs import score_submission
from tasks.linux_runtime import LinuxTaskConfig

_setup = BaseTaskSetup()

logger = logging.getLogger(__name__)

TASK_DESCRIPTION = r'''# Pre-Enactment Constitutional Review

You are an Assistant Solicitor General in the Office of the Attorney General of the State of
Arden. The Climate Resilience Funding Act ("CRFA" or "the bill") has passed both chambers of the
legislature and is now before the Governor for signature. The Governor's office has asked this
Office to deliver, before the signature deadline, a constitutional risk review of the bill
together with amendments that can be adopted as drafted.

The bill establishes the Arden Resilience Fund. It is funded by two classes of charge: a one-time
assessment and an annual revenue charge levied on fossil fuel "Producers" as defined in the bill,
and a property fee levied on the purchase or major renovation of certain property in designated
Endangerment Zones.

Read the two items that frame this review: the Governor's letter to the Attorney General and this
Office's internal assignment memorandum, items 0174 and 0180 in the file. They set out what is
wanted and in what form.

## The file

`input/` is a flat file of 121 numbered documents (92 PDF, 29 XLSX),
numbered from 0002 to 0188. The numbering follows the order in which items were
received and skips: not every item requested was produced.

- `00_FILE_INDEX.pdf` is this Office's index of the file. It lists every item, whether or not a
  document accompanies it, with a short description and the source it came from. Descriptions are
  the ones used in this Office's file and are not always the title on the face of the document.
- `00_MEMORANDUM_TEMPLATE.docx` is the form your deliverable must take.

The file contains materials this review does not need. Working out what bears on the questions and
what does not is part of the task.

## Deliverable

Write one file to `output/memorandum.docx`, built from the supplied template. Nothing else in
`output/` is read.

The template sets out the parts the memorandum must have and the two schedules it must carry. Do
not add, remove, rename or reorder the columns of either schedule. Both schedules are supplied
empty; what belongs in them is for you to decide. Monetary amounts in millions of dollars.

## Constraints

- The authorities are not in the file. Federal statutory text and the decisions of the
  Supreme Court stating the applicable rules are for you to identify and
  cite. A research service is installed under `software/`; start it as its README directs and
  work from what it holds. Read that README on coverage before you treat a nil return as
  settling anything. The Arden constitutional provisions are in the record; nothing else is.
- The state of federal law is as stated in this Office's memorandum on the federal regulatory
  posture, which is in the file. Do not substitute your own knowledge of any later development.
- Every figure that supports a conclusion must appear in Schedule A with its provision and a
  recomputable derivation.
- Cite the smallest lettered or numbered subdivision that carries the point.
- Do not modify anything under `input/`.

'''

DOMAIN_NAME = "legal"
TASK_NAME = "us_crfa_preenactment_review_01"
VARIANT_NAME = "base"

sys.modules.setdefault(__name__, types.ModuleType(__name__))


@dataclass
class CrfaPreEnactmentReviewConfig(LinuxTaskConfig):
    DOMAIN_NAME: str = DOMAIN_NAME
    TASK_NAME: str = TASK_NAME
    VARIANT_NAME: str = VARIANT_NAME

    @property
    def task_brief_file(self) -> str:
        return f"{self.input_dir}/00_TASK_PROMPT.md"

    @property
    def output_file(self) -> str:
        # 交付物是一份 Word,不是 JSON。评分只读这一个文件。
        return f"{self.remote_output_dir}/memorandum.docx"

    @property
    def rubrics_file(self) -> str:
        return f"{self.reference_dir}/rubrics.json"

    @property
    def template_file(self) -> str:
        return f"{self.input_dir}/00_MEMORANDUM_TEMPLATE.docx"

    @property
    def file_index_file(self) -> str:
        return f"{self.input_dir}/00_FILE_INDEX.pdf"

    @property
    def task_description(self) -> str:
        return TASK_DESCRIPTION

    def to_metadata(self) -> dict:
        metadata = super().to_metadata()
        metadata.update(
            {
                "task_brief_file": self.task_brief_file,
                "output_file": self.output_file,
                "rubrics_file": self.rubrics_file,
            }
        )
        # 只登记确实存在的定位性入口。元数据里的死路径会让 agent 去找一个不存在的文件。
        metadata["file_index_file"] = self.file_index_file
        metadata["memorandum_template_file"] = self.template_file
        return metadata


config = CrfaPreEnactmentReviewConfig()


@cb.tasks_config(split="train")
def load():
    cfg = CrfaPreEnactmentReviewConfig()
    return [
        cb.Task(
            description=cfg.task_description,
            metadata=cfg.to_metadata(),
            computer={"provider": "computer", "setup_config": {"os_type": "linux"}},
        )
    ]


async def _missing(session: cb.DesktopSession, path: str) -> bool:
    return not (await session.file_exists(path) or await session.directory_exists(path))


@cb.setup_task(split="train")
async def start(task_cfg, session: cb.DesktopSession):
    await _setup(task_cfg, session)


@cb.evaluate_task(split="train")
async def evaluate(task_cfg, session: cb.DesktopSession) -> list[float]:
    meta = task_cfg.metadata
    tag = meta["variant_name"]

    if await _missing(session, meta["output_file"]):
        logger.error("[%s] Missing output file: %s", tag, meta["output_file"])
        return [0.0]

    # 判据由平台在 agent 交付后 stage 回 reference_dir。缺失是**基础设施故障**,
    # 不是提交缺陷 —— 必须 raise 而不是记 0.0,否则平台故障会被误记成模型失败。
    if await _missing(session, meta["rubrics_file"]):
        raise RuntimeError(
            f"[{tag}] evaluator asset missing: {meta['rubrics_file']} — "
            "private layer was not restored before evaluate()"
        )

    try:
        docx_bytes = await session.read_bytes(meta["output_file"])
    except Exception as exc:
        logger.exception("[%s] Could not read submission: %s", tag, exc)
        return [0.0]
    try:
        rubrics = json.loads(
            (await session.read_bytes(meta["rubrics_file"])).decode("utf-8-sig"))
    except Exception as exc:
        # rubrics 损坏与缺失同属私有层 infra 故障,记 0.0 会冤枉答卷
        raise RuntimeError(
            f"[{tag}] evaluator asset corrupt: {meta['rubrics_file']} — {exc}"
        ) from exc

    # 判分双轨:脚本轨读 Word 里的两张附表,模型轨由独立判官评论证。
    # 判官不可达时 raise —— 那是评测侧的故障,不是答卷的问题;
    # ALE_JUDGE_DISABLED=1 仅供离线冒烟,会让模型轨全部不判。
    from tasks.legal.us_crfa_preenactment_review_01.scripts.score_outputs import (
        JudgeGatewayError,
    )
    use_judge = os.environ.get("ALE_JUDGE_DISABLED") != "1"
    try:
        result = score_submission(docx_bytes, rubrics, judge=use_judge)
    except JudgeGatewayError:
        # 判官通道未配置 = 评测侧 infra 故障,记 0.0 会把平台故障误记成模型失败
        raise
    except Exception as exc:
        logger.exception("[%s] Evaluation failed: %s", tag, exc)
        return [0.0]

    if use_judge:
        unjudged = result.get("model", {}).get("unjudged_weight", 0)
        if unjudged:
            raise RuntimeError(
                f"[{tag}] judge unreachable: {unjudged} points of the reasoning track "
                "could not be graded — scoring would understate the submission"
            )

    logger.info("[%s] evaluation=%s", tag,
                json.dumps({k: v for k, v in result.items() if k != "detail"},
                           ensure_ascii=False, sort_keys=True))
    return [float(result.get("final_score", 0.0))]


if __name__ == "__main__":
    print(__file__)
