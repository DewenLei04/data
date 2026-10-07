#!/usr/bin/env python3
"""Prove the staged finance010 corpus is the one the answers were derived from.

Runs inside the sandbox, before the agent, as the only file besides itself in the
agent-visible `software/` layer. Stdlib only, and streaming: the corpus is a low-thousands-of-
pages tree, so files are hashed in chunks rather than read into memory.

Three checks, each catching something the others miss.

**INTEGRITY.** With `--expect-corpus-digest`, every file under the input root is hashed and
folded into one aggregate digest, compared against the value the caller passes. The caller is
the ALE adapter, which derives that value from `inputInventory` in its own task card -- an
inventory the packager writes from the real tree at build time and `verify_submission()`
recomputes against the packaged tree on every `--verify`. So the number this script is checked
against is not a constant anybody maintains by hand: change one corpus byte and the build fails
before this script is ever reached; change one byte during staging and this script fails. A
single altered cell in a document silently changes what the right answer is, which makes a run
against a mutated corpus a run of a different case, and nothing downstream would notice.

The aggregate covers path AND content, in sorted order, so it fails on a missing file, an extra
file and an edited file alike. UNDECLARED is the direction people forget and the one that
matters most for secrecy: a leaked answer key arrives as an extra file, never as a missing one.

**SECRECY.** The private evaluator layer must not be reachable while the agent runs. This is
also asserted by the adapter against the reference directory, but it is asserted here too and
over a different surface -- the input tree itself -- because the failure being guarded is a
mis-staging, and a mis-staging is exactly the event that would put a private artifact somewhere
neither party expected.

**USABILITY.** A zero-length file passes any hash comparison that was computed over the same
zero-length file, so length is checked separately. An empty document is an answer anchor that
cannot be read, and an unanswerable question scores identically to a hard one -- which is the
one way this benchmark can be wrong without looking wrong.

Exit 0 and `INPUTS_OK` on success; exit 1 with one line per problem otherwise. It never raises
a traceback on a corpus problem: the adapter turns a non-zero exit into a refusal to start,
which is the intended outcome, and a traceback would only obscure which check failed.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

_CHUNK = 1024 * 1024

# Matched case-insensitively. `expected.json` leaks exactly as much as `EXPECTED.json`, and a
# rename is the cheapest possible evasion of a case-sensitive check.
PRIVATE_NAMES: tuple[str, ...] = (
    "EXPECTED.json",
    "traps.json",
    "DESIGN-NUMERIC.md",
    "DESIGN-TRAPS.md",
    "SLICE_TIEOUT.json",
    "HARDENING_BASELINE.json",
    "SCORER_FREEZE.json",
    "score_outputs.py",
    "rubric_registry.py",
    "solve_reference.py",
    "mk_expected.py",
    "mk_trap_manifest.py",
    "mk_positive_fixture.py",
    "slice_solve.py",
    "evaluator_metadata.json",
)

PRIVATE_DIRECTORIES: tuple[str, ...] = ("reference", "fixtures", ".planning", "tools")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_files(input_root: Path) -> list[str]:
    return sorted(
        path.relative_to(input_root).as_posix()
        for path in input_root.rglob("*")
        if path.is_file()
    )


def corpus_digest(input_root: Path) -> str:
    """One digest over the whole tree: path and content, in sorted order.

    The folding is deliberately explicit rather than, say, hashing a directory listing: the
    caller computes the identical value from a JSON inventory, in a different process on a
    different machine, so the recipe has to be reproducible from the inventory alone. `path`,
    NUL, hex digest, newline -- NUL because it cannot occur in a POSIX path, so no filename can
    forge a record boundary.
    """
    digest = hashlib.sha256()
    for relative in relative_files(input_root):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(input_root / relative).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def digest_from_inventory(inventory: "list[dict]") -> str:
    """The same digest, computed from a task card's `inputInventory`. Kept next to
    `corpus_digest` on purpose: the two recipes must not be able to drift apart, and the only
    way to make that likely is for a reader to see both at once."""
    digest = hashlib.sha256()
    for entry in sorted(inventory, key=lambda item: str(item["path"])):
        digest.update(str(entry["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry["sha256"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def verify(input_root: Path, expect_digest: "str | None" = None) -> list[str]:
    input_root = Path(input_root)
    if not input_root.is_dir():
        return [f"input root is not a directory: {input_root}"]

    files = relative_files(input_root)
    if not files:
        return [f"input root is empty: {input_root}"]

    problems: list[str] = []

    lowered_names = {name.casefold() for name in PRIVATE_NAMES}
    lowered_dirs = {name.casefold() for name in PRIVATE_DIRECTORIES}
    for relative in files:
        parts = relative.split("/")
        if parts[-1].casefold() in lowered_names:
            problems.append(f"private artifact reachable in input: {relative}")
        for part in parts[:-1]:
            if part.casefold() in lowered_dirs:
                problems.append(f"private directory reachable in input: {relative}")
                break

    empty = [relative for relative in files if (input_root / relative).stat().st_size == 0]
    problems.extend(f"corpus file is zero length: {relative}" for relative in empty)

    if expect_digest is not None:
        actual = corpus_digest(input_root)
        if actual != expect_digest:
            problems.append(
                "corpus digest mismatch: the staged input tree is not the one this case's "
                f"answers were derived from (expected {expect_digest}, got {actual}; "
                f"{len(files)} file(s) hashed)"
            )

    return problems


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input_root", type=Path)
    parser.add_argument(
        "--expect-corpus-digest",
        default=None,
        help=(
            "aggregate SHA-256 over the whole corpus, as produced by digest_from_inventory() "
            "over the task card's inputInventory. Omitted, the structural checks still run."
        ),
    )
    args = parser.parse_args(argv)

    problems = verify(args.input_root, args.expect_corpus_digest)
    if problems:
        for problem in problems[:100]:
            print(problem)
        if len(problems) > 100:
            print(f"... and {len(problems) - 100} more")
        return 1
    print("INPUTS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
