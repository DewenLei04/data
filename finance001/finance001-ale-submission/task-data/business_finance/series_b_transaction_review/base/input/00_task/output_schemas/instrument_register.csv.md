# `output/instrument_register.csv`

UTF-8 CSV with this exact column order:

1. `instrument_id`
2. `holder`
3. `instrument_type`
4. `principal_or_purchase_amount`
5. `currency`
6. `fx_rate`
7. `interest_rate`
8. `day_count_convention`
9. `accrual_days`
10. `accreted_amount`
11. `valuation_cap`
12. `cap_basis_code`
13. `cap_price`
14. `discount_price`
15. `binding_price`
16. `binding_basis`
17. `special_terms_applied`
18. `special_terms_source`
19. `shares`
20. `discrepancy`
21. `evidence_file`
22. `evidence_section`

Currency display uses two decimal places and per-share values use six decimal places. The governing instruments and policies define the calculation methods and applicability.

## Rows

One row per instrument. **Row order is not significant** — rows are identified by `instrument_id`, not by position.

`instrument_id` is the identifier the corpus already uses for that instrument — the one carried by the instrument's own document and by the register that lists the instruments — not an identifier of your own devising. It is matched ignoring case, surrounding whitespace, and any file extension. Rows for instruments that do not convert belong in this register too and are read for their own purposes.

## Controlled values

These columns take one of a fixed set of tokens. Which token applies to which instrument comes from the governing instruments, not from this schema.

| Column | Permitted values |
|---|---|
| `cap_basis_code` | `Q`, `Q_INC`, `Q_INC_CONV`, or the token the instrument itself uses where it names no capitalisation basis |
| `binding_basis` | `cap` or `discount` |
| `special_terms_applied` | `true` or `false` |
| `special_terms_source` | the `instrument_id` of the instrument whose terms were adopted; empty when no special term applied |
