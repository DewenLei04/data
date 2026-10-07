"""case/scripts/hash_sidecar.py - prove `input/` is exactly what was shipped.

The corpus is read-only source data, and the whole case rests on it being unmodified: every canonical
answer is derived from figures stated in those documents, so a single edited cell silently changes what
the right answer is. A run against a mutated corpus is not a harder or easier run, it is a run of a
different case, and nothing downstream would notice.

So `start()` verifies three things rather than one. Each catches a failure the others miss:

    MISSING     - a declared file is absent. The question it anchors becomes unanswerable, and an
                  unanswerable question scores identically to a hard one.
    UNDECLARED  - a file is present that the sidecar never listed. This is the direction people forget,
                  and it is the one that matters for integrity: a leaked answer key arrives as an
                  EXTRA file, not a missing one.
    MISMATCH    - a declared file's bytes changed. Same name, different case.

Stdlib only, and streaming: the corpus runs to thousands of pages, so hashing reads in chunks rather
than loading a document into memory to fingerprint it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SIDECAR_FILENAME = "input_hashes.json"

_CHUNK = 1024 * 1024

CHECK_MISSING = "MISSING"
CHECK_UNDECLARED = "UNDECLARED"
CHECK_MISMATCH = "MISMATCH"


def sha256_file(path: "str | Path") -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_files(root: Path) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.name != SIDECAR_FILENAME
    )


def generate_sidecar(input_root: "str | Path", out_path: "str | Path | None" = None) -> dict:
    """Fingerprint every file under ``input_root``. Deterministic: sorted keys, fixed separators."""
    root = Path(input_root)
    if not root.is_dir():
        raise FileNotFoundError(f"{root} is not a directory")

    entries = {rel: sha256_file(root / rel) for rel in _relative_files(root)}
    payload = {"algorithm": "sha256", "file_count": len(entries), "files": entries}

    target = Path(out_path) if out_path else root / SIDECAR_FILENAME
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def verify_inputs(input_root: "str | Path", sidecar_path: "str | Path | None" = None) -> list[str]:
    """Return a list of findings, empty iff `input/` matches its sidecar exactly.

    Findings are strings shaped ``"<CHECK>: <relpath>"`` so a caller can both count them and show them.
    A missing sidecar is itself a finding: it means nothing can be verified, which must never read as
    verified.
    """
    root = Path(input_root)
    sidecar = Path(sidecar_path) if sidecar_path else root / SIDECAR_FILENAME

    if not root.is_dir():
        return [f"{CHECK_MISSING}: {root} (input directory absent)"]
    if not sidecar.is_file():
        return [f"{CHECK_MISSING}: {sidecar.name} (no sidecar, so nothing can be verified)"]

    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{CHECK_MISMATCH}: {sidecar.name} (unreadable sidecar: {exc})"]

    declared: dict = payload.get("files") or {}
    present = set(_relative_files(root))
    findings: list[str] = []

    for rel in sorted(declared):
        if rel not in present:
            findings.append(f"{CHECK_MISSING}: {rel}")
            continue
        if sha256_file(root / rel) != declared[rel]:
            findings.append(f"{CHECK_MISMATCH}: {rel}")

    # The direction that matters for integrity: a leaked answer key arrives as an extra file.
    for rel in sorted(present - set(declared)):
        findings.append(f"{CHECK_UNDECLARED}: {rel}")

    return findings
