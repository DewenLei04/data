#!/usr/bin/env python3
"""Verify the staged finance001 input tree against its SHA-256 sidecar."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


HASH_SIDECAR = "POST_CONVERT_HASHES.json"


def verify(input_root: Path) -> list[str]:
    input_root = Path(input_root)
    sidecar = input_root / HASH_SIDECAR
    if not sidecar.is_file():
        return [f"missing input hash sidecar: {sidecar}"]
    try:
        declared = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"unreadable input hash sidecar: {type(exc).__name__}"]
    if not isinstance(declared, dict):
        return ["input hash sidecar must be a JSON object"]

    present = {
        path.relative_to(input_root).as_posix()
        for path in input_root.rglob("*")
        if path.is_file() and path.name != HASH_SIDECAR
    }
    problems = [f"input file absent: {name}" for name in sorted(set(declared) - present)]
    problems.extend(
        f"input file not declared: {name}" for name in sorted(present - set(declared))
    )
    for name in sorted(set(declared) & present):
        digest = hashlib.sha256((input_root / name).read_bytes()).hexdigest()
        if digest != declared[name]:
            problems.append(f"input file altered: {name}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_root", type=Path)
    args = parser.parse_args()
    problems = verify(args.input_root)
    if problems:
        for problem in problems[:100]:
            print(problem)
        return 1
    print("INPUTS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

