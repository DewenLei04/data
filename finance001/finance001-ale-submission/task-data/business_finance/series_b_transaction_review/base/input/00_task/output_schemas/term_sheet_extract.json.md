# `output/term_sheet_extract.json`

UTF-8 JSON object.

Top-level keys, in order:

1. `version_arbitration` — object with these keys: `reviewed_files` (array), `governing` (the selected file), `selection_evidence`, and `signature_date_evidence`.
2. `terms` — array of normalized term objects.

Each item in `terms` has these keys, in order:

1. `term_id` — string.
2. `value` — string, number, boolean, null, object, or array as appropriate.
3. `governing_file` — string.
4. `section` — string.
5. `superseded_values` — array of objects with `value`, `file`, and `section`.

Use identifiers and value definitions required by the governing policy in the input corpus. Do not add unspecified top-level keys.
