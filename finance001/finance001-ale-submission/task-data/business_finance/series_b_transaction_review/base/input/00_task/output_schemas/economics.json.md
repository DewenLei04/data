# `output/economics.json`

UTF-8 JSON object containing the solved transaction economics.

Each reported item must carry its value, unit, source files, source sections, and workbook calculation reference. Write an item either as a bare JSON value, or as an object whose `value` key holds the value and whose remaining keys hold that provenance:

```json
"pps": {"value": "4.000000", "unit": "USD/share",
        "source_files": ["..."], "source_sections": ["..."], "workbook_ref": "03_PoolSolve!C7"}
```

Both forms are read the same way. The value itself is what is scored; the provenance is required by the governing deliverable style guide.

## Required top-level keys

Report each of these at the **top level of the object**, spelled exactly as shown. Do not nest them inside thematic sections; a correct value under a different key or at a different depth is not reported.

`outer_iterations`, `base_shares`, `net_warrant_shares`, `warrant_shares_full_exercise`, `convertible_shares_total`, `plan_reserve_unissued`, `pool_increase`, `pre_money_fd_Q`, `pps`, `round_shares_t1`, `round_shares_full`, `ardent_shares_t1`, `fd_post_first_close`, `ardent_ownership_first_close_pct`, `ardent_shares_full`, `fd_fully_funded`, `ardent_ownership_full_pct`, `pool_pct_post_t1`, `effective_pre_money`, `headline_post_money`

Ownership keys ending `_pct` are percentages, not fractions. `pre_money_fd_Q` is the pre-money fully diluted share count the price is solved against, and `pps` the resulting price per share.

## Required top-level structures

`waterfall_grid` — object with exactly two keys, `modelled` and `consentable`. Each is an array with one entry per exit value in the governing scenario grid, **in ascending exit-value order**. Each entry carries `exit_value`, `carve_out`, `residual_to_common`, `total_distributed`, `elections`, and `capped_series`. `elections` is an object keyed `seed`, `series_a`, `series_b` with boolean values. `capped_series` is an array of the series keys that hit their cap, empty where none do.

`returns` — array with one entry per exit value in the same grid, in the same ascending order, each carrying `exit_value`, `moic_round`, `moic_fund`, `gross_xirr_reported`, and `net_xirr`.

Additional keys and sections beyond the above are permitted and ignored.

JSON values may be numbers, strings, booleans, nulls, arrays, or objects. Computational and display conventions, and which holders exist, come from the governing corpus, not from this schema.
