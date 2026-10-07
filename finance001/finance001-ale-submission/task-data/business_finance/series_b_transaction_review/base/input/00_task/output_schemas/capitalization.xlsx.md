# `output/capitalization.xlsx`

A valid XLSX (OOXML) workbook containing the schedules required by the governing deliverable style guide. The workbook must open in a spreadsheet application: the package must contain `[Content_Types].xml`, `_rels/.rels`, `xl/workbook.xml` and `xl/_rels/workbook.xml.rels`, and every sheet declared in the workbook must have its own worksheet part.

Use exactly these nine sheets, in this order, spelled exactly as shown:

1. `01_PreFinancing`
2. `02_Convertibles`
3. `03_PoolSolve`
4. `04_PostFinancing`
5. `05_Waterfall`
6. `06_AntiDilution`
7. `07_Returns`
8. `08_Sensitivity`
9. `09_TieOut`

Add no other sheets and rename none of them. Named ranges, evidence columns, and display formats follow the governing deliverable style guide; this structural pointer does not repeat them.

Every table must have a header row, stable identifiers, and source-file and source-section columns where evidence is required. Do not hide rows, columns, or sheets, and do not leave a sheet empty.

Cells marked as calculated in the governing style guide must contain both a live formula and a cached numeric value; a result pasted without its formula, or a formula saved without its cached result, does not satisfy this. Every sheet carries at least one such calculated cell.

The share counts, price per share, and fully diluted totals the workbook reports must be present in it as cached numeric values, and must agree with the same quantities reported in `output/economics.json`. The governing corpus defines calculations, inclusion rules, and presentation precision.
