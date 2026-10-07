# `output/redline_positions.csv`

UTF-8 CSV with this exact column order:

1. `term_id`
2. `section`
3. `current_position`
4. `proposed_position`
5. `fallback_position`
6. `priority_rank`
7. `concede`
8. `evidence_file`
9. `evidence_section`
10. `notes`

`priority_rank` is an integer. `concede` is `true` or `false`. Use stable term identifiers that tie to the extract and deviation register. Applicability and position content come from the governing corpus.
