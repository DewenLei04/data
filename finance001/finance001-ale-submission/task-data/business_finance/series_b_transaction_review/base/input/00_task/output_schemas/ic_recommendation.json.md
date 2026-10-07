# `output/ic_recommendation.json`

UTF-8 JSON object with these keys, in order:

1. `recommendation` — string.
2. `gate_results` — array.
3. `conditions_precedent` — array.
4. `required_consents` — array.
5. `remedies` — array.
6. `evidence` — array.

Each gate result contains `gate_id`, `result`, `measurement`, `value`, `limit`, `unit`, `source_file`, and `source_section`.

Each remedy contains `remedy_id`, `description`, `quantified_effect`, `unit`, `feasible`, `required_actions`, and evidence fields. Use two decimal places for currency and four decimal places for percentages. The governing corpus defines the gates, remedies, precedence, and applicability.
