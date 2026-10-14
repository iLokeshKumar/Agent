# Policy: P1 Purchase Intake

## Approval thresholds (INR)
| Total | Route |
|---|---|
| <= 50,000 | auto (within budget) |
| 50,001 – 500,000 | Branch Head |
| 500,001 – 2,500,000 | GM |
| > 2,500,000 | MD + finance concurrence |

## Validation rules
- quantity: integer-like > 0
- unit_price: >= 0; missing price is an error, not a zero
- vendor: non-empty string

## Budget semantics
- `budget_limit` absent → NOT over-budget at agent level; value caps
  are the platform Policy Gate's job (risk classifications), not this
  skill's. Tracked gap: seed a default per-request cap so an
  undeclared limit cannot mean unlimited spend.
- Comparison uses Decimal; a total exactly equal to the limit is NOT
  over budget.

## Tone (vendor ack)
- Formal business English. No emojis. Include PO number and expected
  delivery ask.
