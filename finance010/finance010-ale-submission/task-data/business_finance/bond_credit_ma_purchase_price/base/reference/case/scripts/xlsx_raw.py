"""case/scripts/xlsx_raw.py - a stdlib-only raw-XML `.xlsx` reader that never trusts a
cached formula value (SCORE-05/06, RESEARCH Q2(a)/(c)/(d)).

Why raw XML and not a third-party spreadsheet-object library: that ecosystem's cached-
values-only load path returns whatever numeric value the last WRITING application cached
into `<v>` - it never executes a formula itself, so an agent-authored submission can
carry a stale, absent, or outright fabricated cached value behind a live-looking `<f>`.
This module parses `xl/worksheets/sheetN.xml` directly with `zipfile` +
`xml.etree.ElementTree` (mirroring finance001's `_worksheet_facts` approach per
ARCHITECTURE.md Sec4) so a caller can see the formula text and the cached value as two
separate, honestly-labelled facts and decide for itself which one it trusts.
`cached_numbers()` is deliberately named to say "cached", not "value" or "result" - its
docstring says outright that its output is only trustworthy for a workbook that has
already been through `recalc.trusted_values`.

This module never imports that third-party library and never reads its cached-values-
only load flag anywhere - both facts are enforced by a grep gate in `02-04-PLAN.md`'s
acceptance criteria.

`read_workbook` is a never-raise boundary (RESEARCH Security Domain V5): a non-zip file, a
zip missing an expected part, or a malformed XML part is recorded as a named string in
`WorkbookFacts.shape_errors` and parsing continues with whatever was salvageable - it never
raises on a hostile or malformed submission. `XlsxShapeError` below is reserved for
programmer error in this module's own helpers (a caller passing the wrong argument shape)
and is never raised while parsing a submission.
"""

import re
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class XlsxShapeError(Exception):
    """Programmer error in this module's own helpers only - never raised while parsing a
    submitted workbook. A submission-side failure is always recorded as a named string in
    `WorkbookFacts.shape_errors`, never raised as an exception."""


@dataclass(frozen=True)
class CellFact:
    """One `<c>` element's facts, formula and cached value kept honestly separate.

    Fields:
        sheet: the declared sheet name this cell belongs to.
        ref: the cell reference (e.g. "B5"), exactly as written in the `r` attribute.
        formula: the `<f>` child's text with any leading "=" stripped, or `None` if the
            cell has no `<f>` child at all. Present (even as `""`) iff the cell is
            calculated - see `is_calculated`.
        cached_raw: the `<v>` child's raw text exactly as stored, or `None` if absent.
            For a `t="s"` cell this is a shared-string TABLE INDEX, not literal text -
            never compare this field directly to an expected value; use `value_text`.
        cell_type: the `<c>` element's `t` attribute verbatim (`"s"`, `"str"`, `"b"`,
            `"e"`, `"inlineStr"`), or `None` for the default numeric type.
        value_text: the human-meaningful text for this cell: `cached_raw` for numeric
            cells, the resolved shared-string/inline-string text for `t="s"`/`"inlineStr"`
            cells. Still a CACHED fact, not a trusted computed value.
    """

    sheet: str
    ref: str
    formula: str | None
    cached_raw: str | None
    cell_type: str | None
    value_text: str | None


@dataclass(frozen=True)
class WorkbookFacts:
    """Everything this module could recover from one `.xlsx` package.

    `cells` is keyed by `(sheet, ref)` so a lookup never depends on sheet declaration
    order. `shape_errors` is never raised - see the module docstring.
    """

    path: Path
    sheets: tuple[str, ...]
    cells: dict[tuple[str, str], CellFact]
    defined_names: dict[str, str]
    shared_strings: tuple[str, ...]
    shape_errors: tuple[str, ...] = field(default_factory=tuple)


def _rich_text(el: ET.Element) -> str:
    """Concatenate a `<si>`/`<is>` element's direct `<t>` text and any rich-text `<r>/<t>`
    run texts, in document order, per RESEARCH Q2's shared-string path table."""
    parts: list[str] = []
    direct = el.find(f"{NS_MAIN}t")
    if direct is not None and direct.text:
        parts.append(direct.text)
    for run in el.findall(f"{NS_MAIN}r"):
        run_t = run.find(f"{NS_MAIN}t")
        if run_t is not None and run_t.text:
            parts.append(run_t.text)
    return "".join(parts)


def _resolve_part(target: str) -> str:
    """A `.rels` `Target` attribute is relative to the `xl/` directory (or occasionally
    package-root-absolute with a leading "/") - never assume sheet index equals file
    number; always resolve through the relationship map."""
    if target.startswith("/"):
        return target.lstrip("/")
    return "xl/" + target


def _resolve_value_text(
    t_attr: str | None,
    cached_raw: str | None,
    c_el: ET.Element,
    shared_strings: tuple[str, ...],
    shape_errors: list[str],
    sheet_name: str,
    ref: str,
) -> str | None:
    if t_attr == "s":
        if cached_raw is None:
            return None
        try:
            idx = int(cached_raw)
        except ValueError:
            shape_errors.append(f"bad_xml:sharedStrings_index:{sheet_name}:{ref}")
            return None
        if 0 <= idx < len(shared_strings):
            return shared_strings[idx]
        shape_errors.append(f"bad_xml:sharedStrings_index:{sheet_name}:{ref}")
        return None
    if t_attr == "inlineStr":
        is_el = c_el.find(f"{NS_MAIN}is")
        if is_el is None:
            return None
        return _rich_text(is_el)
    return cached_raw


def _parse_sheet(
    sheet_xml: bytes,
    sheet_name: str,
    cells: dict[tuple[str, str], CellFact],
    shared_strings: tuple[str, ...],
    shape_errors: list[str],
) -> None:
    root = ET.fromstring(sheet_xml)
    sheet_data = root.find(f"{NS_MAIN}sheetData")
    if sheet_data is None:
        return

    c_elements = [c for row in sheet_data.findall(f"{NS_MAIN}row") for c in row.findall(f"{NS_MAIN}c")]

    # First pass: collect shared-formula masters (t="shared" with formula text present)
    # by their `si` index, per RESEARCH Q2(a) and the never-invent-a-master rule.
    shared_masters: dict[str, str] = {}
    for c in c_elements:
        f_el = c.find(f"{NS_MAIN}f")
        if f_el is not None and f_el.get("t") == "shared" and f_el.text and f_el.get("si") is not None:
            shared_masters[f_el.get("si")] = f_el.text.strip().lstrip("=")

    for c in c_elements:
        ref = c.get("r")
        if ref is None:
            continue
        t_attr = c.get("t")
        f_el = c.find(f"{NS_MAIN}f")
        v_el = c.find(f"{NS_MAIN}v")

        formula: str | None = None
        if f_el is not None:
            if f_el.get("t") == "shared":
                if f_el.text:
                    formula = f_el.text.strip().lstrip("=")
                else:
                    si = f_el.get("si")
                    master = shared_masters.get(si) if si is not None else None
                    if master is not None:
                        formula = master
                    else:
                        shape_errors.append(f"shared_formula_no_master:{sheet_name}:{ref}")
                        # The cell IS calculated (an <f> element is present) even though
                        # its text could not be resolved - never invent a formula string,
                        # but never lose the calculated fact either.
                        formula = ""
            else:
                formula = (f_el.text or "").strip().lstrip("=")

        cached_raw = v_el.text if v_el is not None else None
        value_text = _resolve_value_text(
            t_attr, cached_raw, c, shared_strings, shape_errors, sheet_name, ref
        )
        cells[(sheet_name, ref)] = CellFact(sheet_name, ref, formula, cached_raw, t_attr, value_text)


def read_workbook(path: Path) -> WorkbookFacts:
    """Parse one submitted `.xlsx` into `WorkbookFacts`. Never raises - see module
    docstring. Reads only the fixed allow-listed OOXML parts by exact name (T-02-06):
    `xl/workbook.xml`, `xl/_rels/workbook.xml.rels`, `xl/worksheets/sheetN.xml` (resolved
    through the rels map, never assumed), `xl/sharedStrings.xml`."""
    path = Path(path)
    shape_errors: list[str] = []
    sheet_names: tuple[str, ...] = ()
    cells: dict[tuple[str, str], CellFact] = {}
    defined_names: dict[str, str] = {}
    shared_strings: tuple[str, ...] = ()

    try:
        try:
            zf = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError):
            return WorkbookFacts(path, (), {}, {}, (), ("not_a_zip",))

        with zf:
            try:
                if zf.testzip() is not None:
                    shape_errors.append("bad_xml:zip_member_crc")
            except Exception:
                shape_errors.append("bad_xml:zip_member_crc")

            sheet_decls: list[tuple[str, str | None]] = []
            try:
                wb_xml = zf.read("xl/workbook.xml")
            except KeyError:
                shape_errors.append("missing_part:xl/workbook.xml")
                wb_xml = None

            if wb_xml is not None:
                try:
                    wb_root = ET.fromstring(wb_xml)
                except ET.ParseError:
                    shape_errors.append("bad_xml:xl/workbook.xml")
                    wb_root = None
                if wb_root is not None:
                    sheets_el = wb_root.find(f"{NS_MAIN}sheets")
                    if sheets_el is not None:
                        for sheet_el in sheets_el.findall(f"{NS_MAIN}sheet"):
                            name = sheet_el.get("name")
                            rid = sheet_el.get(f"{NS_REL}id")
                            if name is not None:
                                sheet_decls.append((name, rid))
                    dn_el = wb_root.find(f"{NS_MAIN}definedNames")
                    if dn_el is not None:
                        for dn in dn_el.findall(f"{NS_MAIN}definedName"):
                            dn_name = dn.get("name")
                            if dn_name is not None:
                                defined_names[dn_name] = (dn.text or "").strip()

            rid_to_target: dict[str, str] = {}
            try:
                rels_xml = zf.read("xl/_rels/workbook.xml.rels")
            except KeyError:
                shape_errors.append("missing_part:xl/_rels/workbook.xml.rels")
                rels_xml = None

            if rels_xml is not None:
                try:
                    rels_root = ET.fromstring(rels_xml)
                except ET.ParseError:
                    shape_errors.append("bad_xml:xl/_rels/workbook.xml.rels")
                    rels_root = None
                if rels_root is not None:
                    for rel in rels_root.findall(f"{NS_PKG_REL}Relationship"):
                        rel_id = rel.get("Id")
                        target = rel.get("Target")
                        if rel_id is not None and target is not None:
                            rid_to_target[rel_id] = target

            try:
                ss_xml = zf.read("xl/sharedStrings.xml")
            except KeyError:
                ss_xml = None  # optional part - its absence is not an error

            if ss_xml is not None:
                try:
                    ss_root = ET.fromstring(ss_xml)
                    shared_strings = tuple(
                        _rich_text(si) for si in ss_root.findall(f"{NS_MAIN}si")
                    )
                except ET.ParseError:
                    shape_errors.append("bad_xml:xl/sharedStrings.xml")

            sheet_names = tuple(name for name, _ in sheet_decls)
            for name, rid in sheet_decls:
                target = rid_to_target.get(rid) if rid is not None else None
                if target is None:
                    shape_errors.append(f"missing_part:sheet_rel:{name}")
                    continue
                part = _resolve_part(target)
                try:
                    sheet_xml = zf.read(part)
                except KeyError:
                    shape_errors.append(f"missing_part:{part}")
                    continue
                try:
                    _parse_sheet(sheet_xml, name, cells, shared_strings, shape_errors)
                except ET.ParseError:
                    shape_errors.append(f"bad_xml:{part}")
    except Exception as exc:  # never-raise boundary of last resort (RESEARCH V5)
        shape_errors.append(f"bad_xml:unexpected:{type(exc).__name__}")

    return WorkbookFacts(path, sheet_names, cells, defined_names, shared_strings, tuple(shape_errors))


def is_calculated(facts: WorkbookFacts, sheet: str, ref: str) -> bool:
    """True iff the cell at (sheet, ref) has a formula (an `<f>` child), regardless of
    whether a cached `<v>` is also present (RESEARCH Q2(d))."""
    cell = facts.cells.get((sheet, ref))
    return cell is not None and cell.formula is not None


def is_hardcoded_constant(facts: WorkbookFacts, sheet: str, ref: str) -> bool:
    """True iff the cell at (sheet, ref) carries a value (cached numeric/string text) but
    has NO formula - the exact complement of `is_calculated` for any cell that exists at
    all (RESEARCH Q2(d))."""
    cell = facts.cells.get((sheet, ref))
    if cell is None or cell.formula is not None:
        return False
    return cell.cached_raw is not None or cell.value_text is not None


_DEFINED_NAME_TARGET_RE = re.compile(
    r"^(?:'(?P<qname>[^']+)'|(?P<name>[A-Za-z0-9_. ]+))!"
    r"\$?(?P<col1>[A-Za-z]+)\$?(?P<row1>\d+)"
    r"(?::\$?[A-Za-z]+\$?\d+)?$"
)


def resolve_defined_name(facts: WorkbookFacts, name: str) -> tuple[str, str] | None:
    """Resolve a defined name to its `(sheet, ref)` anchor cell.

    Handles `Sheet1!$B$5`, `'Model Sheet'!$B$5`, and `Sheet1!$B$5:$B$9` (a range resolves
    to its anchor cell only). A defined name with no `!` (a workbook-scoped local
    reference with no sheet qualifier) or an unresolvable target returns `None` -
    RESEARCH Q2(c) never guesses a sheet.
    """
    target = facts.defined_names.get(name)
    if target is None:
        for key, value in facts.defined_names.items():
            if key.casefold() == name.casefold():
                target = value
                break
    if not target:
        return None

    match = _DEFINED_NAME_TARGET_RE.match(target.strip())
    if not match:
        return None

    sheet = match.group("qname") or match.group("name")
    ref = f"{match.group('col1')}{match.group('row1')}"
    return (sheet, ref)


def named_range_resolves(facts: WorkbookFacts, name: str) -> bool:
    """True iff the defined name resolves to a real workbook reference - an anchor cell on
    an actually-declared sheet - never merely existing (`financial_model.schema.json`'s own
    `must_reference_range`: "the name must actually resolve to a workbook reference rather
    than merely exist"). This is a property of the DEFINED NAME ITSELF; it deliberately does
    NOT ask whether any OTHER cell's formula cites this name by identifier.

    04-09-PLAN.md: `named_range_used` (this function's predecessor, deleted - nothing else in
    this codebase called it) added exactly that citation requirement, which the published
    schema never states. Real models reference cells BY ADDRESS
    (`='Market & Bonds'!B45`) and carry defined names only as a labelling layer - measured
    against probe2's real submission, 26/26 declared names resolve to a real reference here
    while only 14/26 are ever cited inside another formula, so the citation rule was failing
    12 items for a rule the agent was never given.

    Still non-vacuous: `resolve_defined_name` already returns `None` for a target string that
    does not parse as `Sheet!$Col$Row` (or a range) - a `#REF!` or a bare literal constant
    (`myName = 42`) never matches that grammar. The one check this function adds on top of
    `resolve_defined_name`: the resolved sheet must be one this workbook actually declares - a
    name that parses as `GhostSheet!$B$5` for a sheet that does not exist is dangling, not a
    real reference, even though its target STRING is well-formed."""
    target = resolve_defined_name(facts, name)
    if target is None:
        return False
    sheet, _ref = target
    declared = {s.casefold() for s in facts.sheets}
    return sheet.casefold() in declared


_DOUBLE_QUOTED_STRING_RE = re.compile(r'"[^"]*"')
# A digit-adjacent E/e is a scientific-notation exponent (`1E10`), never an identifier
# letter - stripped before the literal-detection scan below so a bare numeric literal
# written in exponential form is not mistaken for a reference to column E, row 10.
_SCI_NOTATION_EXPONENT_RE = re.compile(r"(?<=[0-9])[eE](?=[+-]?[0-9])")
_ANY_LETTER_RE = re.compile(r"[A-Za-z]")


def formula_is_literal(formula_text: "str | None") -> bool:
    """True when a formula's body is a constant wearing an equals sign - no cell
    reference (`A1`, `$B$7`, `Sheet1!C3`, a quoted sheet name like `'Market & Bonds'!B45`,
    a range `B2:B9`), no function call (`SUM(...)`, `IF(...)`), and no reference to
    ANOTHER workbook-defined name (`=some_other_named_range`) - `=1+1` and
    `=180705729.17` are the two forms this exists to catch (04-07-PLAN.md Task 1; the
    docstring `named_range_is_calculated` used to carry named this exact gap and left it
    open: it scoped WHERE to look, never WHAT counts once there).

    The rule, deliberately simple rather than a hand-rolled reference grammar: after
    removing double-quoted string literals (`="capped"`) and scientific-notation
    exponents (`1E10`), a literal formula's remaining text contains NO alphabetic
    character at all. Every one of the non-literal forms above - a cell reference, a
    range, a function name, a quoted OR unquoted sheet-qualifier, a bare reference to
    another defined name - necessarily contains at least one letter; a bare arithmetic
    expression over numbers never does. This sidesteps needing to separately special-case
    every one of those forms (including the one PLAN.md's own hard constraint flags: "a
    formula referencing another workbook name rather than an address" - a bare name
    reference has no digits or `!` at all, so a regex built around cell-address SHAPE
    would have missed it entirely; this rule catches it for free because the name is
    still letters).

    Known, accepted scope limit: a formula whose ONLY letters are a literal `TRUE`/`FALSE`
    boolean constant would be (mis)classified as non-literal by this rule. No
    `RANGE_CALCULATED` target in this build's `FINANCIAL_MODEL_NAMED_RANGES` is
    boolean-kind, so this never actually arises against the published vocabulary - noted
    here rather than silently relied upon.

    `None` (no formula at all) returns `True` (vacuously "not a real calculation") -
    callers that need to distinguish "no formula" from "a literal formula" should check
    presence separately, exactly as `named_range_is_calculated` does below.
    """
    if not formula_text:
        return True
    text = _DOUBLE_QUOTED_STRING_RE.sub("", formula_text)
    text = _SCI_NOTATION_EXPONENT_RE.sub("", text)
    return _ANY_LETTER_RE.search(text) is None


def named_range_is_calculated(facts: WorkbookFacts, name: str) -> bool:
    """Composes `resolve_defined_name` + `is_calculated` + `formula_is_literal` so a
    caller can assert REAL formula-derivation at THE SPECIFIC published named-range
    target - never "the file contains at least one `<f>` anywhere" (PITFALLS Sec3.1's
    `=1+1` failure), and never "an `<f>` element is present" alone either, which a bare
    `=180705729.17` (a hard-coded figure wearing an equals sign) also satisfies
    (04-07-PLAN.md Task 1 - the gap this composition closes)."""
    target = resolve_defined_name(facts, name)
    if target is None:
        return False
    if not is_calculated(facts, target[0], target[1]):
        return False
    cell = facts.cells.get(target)
    return not formula_is_literal(cell.formula if cell is not None else None)


def cached_numbers(facts: WorkbookFacts) -> tuple[Decimal, ...]:
    """Every cell's cached value that parses as a plain Decimal number, across the whole
    workbook.

    DOCSTRING WARNING - read before using this function: its output is a CACHED fact only.
    It is trustworthy as a computed result only for a `WorkbookFacts` produced by
    `recalc.trusted_values` (a soffice-recalculated copy). Calling this on a submitted
    workbook's own `WorkbookFacts` and treating the result as "the submission's computed
    answer" is exactly the cached-values-only-load trap this module exists to prevent.

    Accepts cell_type `None` (the implicit default numeric type most writers - openpyxl
    included - emit) AND the explicit `"n"` (LibreOffice's own recalculation pass writes
    an explicit `t="n"` on every numeric `<c>` it recalculates, per ECMA-376 Part 1
    Sec18.3.1.4 - a valid explicit spelling of the same default type, not a different
    kind of cell). Before this fix, every cell LibreOffice had just recalculated was
    silently excluded here - `cell.cell_type not in (None, "str")` treated `"n"` as
    neither the default NOR one of the two named exceptions, so this function returned
    an empty tuple for every real recalculated workbook it was ever pointed at. Never
    caught earlier because nothing called this function against a real recalculated
    file until `case/scripts/cross_file.agrees_across` was finally wired into
    `case/scripts/score_outputs.py` (04-05-PLAN.md Task 2) - the same "dead code, first
    real use reveals the bug" pattern as this project's other ERRATA entries.
    """
    numbers: list[Decimal] = []
    for cell in facts.cells.values():
        if cell.cell_type not in (None, "n", "str") or cell.value_text is None:
            continue
        try:
            numbers.append(Decimal(cell.value_text.strip()))
        except (InvalidOperation, AttributeError):
            continue
    return tuple(numbers)
