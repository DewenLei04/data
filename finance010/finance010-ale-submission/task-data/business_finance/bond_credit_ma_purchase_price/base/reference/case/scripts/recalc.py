"""case/scripts/recalc.py - forced spreadsheet recalculation via a headless
`soffice` subprocess, with a distinct named failure code per failure mode (SCORE-05,
RESEARCH Q2(b)).

Why this module exists at all: an agent-submitted `.xlsx`'s own cached `<v>` values can
never be trusted (see `xlsx_raw.py`'s module docstring). The only way to obtain a
genuinely trustworthy computed value is to hand the submission to a real calculation
engine and read back WHAT IT PRODUCED, not what the agent wrote. `soffice --headless
--convert-to xlsx` is that engine. Exit code 0 is necessary but NOT sufficient proof of
success (RESEARCH Q2(b)) - some builds report success on a partially failed conversion -
so `recalc_to_xlsx` additionally verifies the produced file exists, is nonzero-size, and
is a structurally valid zip before calling the recalculation trustworthy.

One submission = one fresh user-profile directory = exactly one `soffice` invocation.
STACK.md's documented cross-invocation performance degradation is a BATCHING artifact of
running many conversions against a shared/reused profile in one long-lived process; this
module never batches, so that degradation mode structurally cannot occur here. A
scorer-side lock file additionally serializes concurrent invocations against the same
output directory, in case two scoring passes ever run against overlapping paths.

`trusted_values()` is the only function this module expects callers outside this file to
use for obtaining computed facts. Its signature - `(WorkbookFacts | None, str | None)` -
makes a silent recalculation failure that still yields stale values UNREPRESENTABLE: on
any failure it returns `(None, failure_code)`, never a `WorkbookFacts` read from the
submitted copy.
"""

import os
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

import xlsx_raw

SOFFICE_BIN = os.environ.get("SOFFICE_BIN", "soffice")
SOFFICE_TIMEOUT_S = 120

_LOCK_MAX_ATTEMPTS = 25
_LOCK_BACKOFF_S = 0.2

FAILURE_CODES: frozenset[str] = frozenset(
    {
        "recalc_soffice_missing",
        "recalc_nonzero_exit",
        "recalc_timeout",
        "recalc_output_missing",
        "recalc_output_empty",
        "recalc_bad_zip",
        "recalc_profile_lock",
    }
)

# The exact flag set RESEARCH Q2(b) specifies, as a template so tests can assert its
# shape without ever launching the real binary. `{profile_uri}` and `{outdir}`/`{submitted}`
# are filled in by `recalc_to_xlsx`.
#
# `{profile_uri}` - not a hand-built `file:///{profile}` string - is a FULL `file://` URI
# already carrying the right number of slashes/drive-letter form for the host OS
# (`Path.as_uri()`). A hand-built `file:///{profile}` string was correct only for a
# POSIX absolute path; on Windows (the authoring host) it produced an invalid URI that
# soffice silently could not resolve, so every recalculation ran to `SOFFICE_TIMEOUT_S`
# and failed with `recalc_timeout` - never wrong, but never fast, and on this host never
# successful either, which made `recalc.trusted_values` structurally unusable for
# building or testing a real fixture (04-04, Rule 3 - blocking issue).
SOFFICE_ARGV_TEMPLATE: tuple[str, ...] = (
    "{soffice}",
    "--headless",
    "--norestore",
    "--convert-to",
    "xlsx",
    "-env:UserInstallation={profile_uri}",
    "--outdir",
    "{outdir}",
    "{submitted}",
)


@dataclass(frozen=True)
class RecalcResult:
    """The outcome of one `recalc_to_xlsx` invocation.

    `ok=True` iff a recalculated `.xlsx` was produced AND independently verified to
    exist, be nonzero-size, and be a structurally valid zip. `failure` is always one of
    `FAILURE_CODES` when `ok=False`, `None` when `ok=True`.
    """

    ok: bool
    path: Path | None
    failure: str | None
    stderr_tail: str = ""


def _build_argv(soffice: str, profile_dir: str, outdir: Path, submitted: Path) -> list[str]:
    # `Path.as_uri()` requires an absolute path and produces the correct `file://` form
    # for the host OS (`file:///tmp/x` on POSIX, `file:///C:/Users/.../x` on Windows) -
    # never a hand-built string, which only one of those two forms is valid for. The
    # profile directory need not exist yet (soffice creates it); `as_uri()` does not
    # require existence, only absoluteness.
    profile_uri = Path(profile_dir).resolve().as_uri()
    return [
        part.format(soffice=soffice, profile_uri=profile_uri, outdir=str(outdir), submitted=str(submitted))
        for part in SOFFICE_ARGV_TEMPLATE
    ]


def _acquire_lock(outdir: Path) -> Path | None:
    """Bounded retry/backoff `O_CREAT|O_EXCL` lock file scoped to `outdir`. Returns the
    lock path on success, `None` if the retry bound was exceeded (caller maps this to
    `recalc_profile_lock`). Always released by the caller in a `finally` block."""
    lock_path = outdir / ".recalc.lock"
    for _ in range(_LOCK_MAX_ATTEMPTS):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return lock_path
        except FileExistsError:
            time.sleep(_LOCK_BACKOFF_S)
    return None


def _release_lock(lock_path: Path | None) -> None:
    if lock_path is None:
        return
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def recalc_to_xlsx(submitted: Path, outdir: Path, soffice: str = SOFFICE_BIN) -> RecalcResult:
    """Force a `.xlsx` recalculation pass. Never raises - every failure mode maps to a
    named code in `FAILURE_CODES` and `path=None`."""
    submitted = Path(submitted)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    resolved = shutil.which(soffice)
    if resolved is None:
        return RecalcResult(ok=False, path=None, failure="recalc_soffice_missing")

    lock_path = _acquire_lock(outdir)
    if lock_path is None:
        return RecalcResult(ok=False, path=None, failure="recalc_profile_lock")

    try:
        # A fresh, never-reused profile directory under the OS temp root - never a
        # hardcoded POSIX `/tmp` path, which does not exist as a real filesystem root on
        # Windows (see `SOFFICE_ARGV_TEMPLATE`'s docstring note above).
        profile_dir = str(Path(tempfile.gettempdir()) / f"soffice_profile_{uuid.uuid4().hex}")
        argv = _build_argv(resolved, profile_dir, outdir, submitted)
        try:
            proc = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                timeout=SOFFICE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return RecalcResult(ok=False, path=None, failure="recalc_timeout")
        except OSError:
            return RecalcResult(ok=False, path=None, failure="recalc_soffice_missing")

        stderr_tail = (proc.stderr or b"").decode(errors="replace")[-500:]

        if proc.returncode != 0:
            return RecalcResult(
                ok=False, path=None, failure=f"recalc_nonzero_exit:{proc.returncode}", stderr_tail=stderr_tail
            )

        # Exit code 0 is necessary but not sufficient (RESEARCH Q2(b)) - independently
        # verify the produced artifact before trusting it.
        produced = outdir / f"{submitted.stem}.xlsx"
        if not produced.exists():
            return RecalcResult(ok=False, path=None, failure="recalc_output_missing", stderr_tail=stderr_tail)
        if produced.stat().st_size == 0:
            return RecalcResult(ok=False, path=None, failure="recalc_output_empty", stderr_tail=stderr_tail)
        try:
            with zipfile.ZipFile(produced) as zf:
                bad_member = zf.testzip()
        except zipfile.BadZipFile:
            return RecalcResult(ok=False, path=None, failure="recalc_bad_zip", stderr_tail=stderr_tail)
        if bad_member is not None:
            return RecalcResult(ok=False, path=None, failure="recalc_bad_zip", stderr_tail=stderr_tail)

        return RecalcResult(ok=True, path=produced, failure=None, stderr_tail=stderr_tail)
    finally:
        _release_lock(lock_path)


def trusted_values(submitted: Path, workdir: Path) -> tuple["xlsx_raw.WorkbookFacts | None", str | None]:
    """The only computed-value entry point callers outside this module should use.

    On success: `(facts, None)` where `facts` was read through `xlsx_raw.read_workbook`
    from the RECALCULATED copy only. On any failure: `(None, failure_code)` - a caller
    receiving `None` must record `failure_code` as a G0 submission-side shape_error. This
    signature makes "silent soffice failure yielding stale submitted values" structurally
    unrepresentable - there is no code path in this function that returns facts read from
    `submitted` itself.
    """
    result = recalc_to_xlsx(submitted, workdir)
    if not result.ok or result.path is None:
        return None, result.failure
    facts = xlsx_raw.read_workbook(result.path)
    return facts, None
