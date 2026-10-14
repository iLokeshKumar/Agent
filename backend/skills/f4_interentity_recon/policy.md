# F4 Policy

## Approval Gates

| Condition                       | Gate                    |
|---------------------------------|-------------------------|
| Any `confidence=high` match     | Finance Manager review  |
| Any `confidence=ambiguous`      | Finance Controller pick |
| `action=settle`                 | Finance Controller      |

## Constraints

1. `match_status` may only transition: `open → suggested → matched` (or `open → disputed`)
2. No direct jump from `open` to `matched` without human confirmation
3. Amount tolerance is fixed at 0.1 % — never widen for convenience
4. Matches older than 180 days are surfaced separately — do not auto-suggest

## Data

- Source table: `interentity_open_item`
- Output state: `match_status` column (suggested/matched/disputed)
- Suggestion payload stored in `suggestion_json` column
