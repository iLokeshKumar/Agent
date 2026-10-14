# H1 Policy

## Approval Gates

| Condition                          | Gate              |
|------------------------------------|-------------------|
| `action=intake` (new opening)      | HR Manager review |
| `action=update, status=filled`     | HR Manager sign-off|
| JD publication to job portals      | HR Manager only   |

## Constraints

1. H1 never publishes JDs to external portals — HR Manager gate is mandatory
2. `status=filled` must be accompanied by a candidate name or employee_id in notes
3. Target fill date is advisory — agents may not deprioritise older openings
4. Openings for roles with salary > ₹5L/month must be escalated to GM

## Data

- Output table: `job_opening`
- JD draft returned in `output.jd_draft` — not persisted to DB (HR's responsibility to store)
