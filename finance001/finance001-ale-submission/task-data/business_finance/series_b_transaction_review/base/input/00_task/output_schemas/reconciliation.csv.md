# `output/reconciliation.csv`

UTF-8 CSV with this exact column order:

1. `item_id`
2. `source_a_file`
3. `source_a_section`
4. `source_a_value`
5. `source_b_file`
6. `source_b_section`
7. `source_b_value`
8. `agree`
9. `governing_source`
10. `governing_value`
11. `protocol_rule`
12. `downstream_use`

`agree` is `true` or `false`. Use one row per reconciliation item required by the governing protocol. Do not infer relevance from directory or file names alone.
