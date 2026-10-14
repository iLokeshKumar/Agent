# Skill: P1 Purchase Intake

## Identity
Convert purchase requests arriving on WhatsApp, Outlook or the API into
catalogued, budget-checked purchase orders. Suite: Purchase. Runtime:
hybrid (API/webhook intake headless; sourcing judgement supervised).

## Procedure
1. **Validate** the request: vendor present, every item has name,
   quantity > 0, unit_price >= 0.
2. **Calculate** line totals and grand total (Decimal, never float).
3. **Budget gate**: if total exceeds `budget_limit`, raise approval
   (`BUDGET_EXCEEDED`) and stop — never auto-approve.
4. **Generate PO number** from the Postgres sequence.
5. **Create the order** through the procurement gateway.
6. **Report**: po_number, total, over_budget flag, actions taken.

## Tool bindings
| Tool | Use |
|---|---|
| procurement gateway | PO creation |
| numbering service | PO sequence |

## Autonomy & human-in-the-loop
- A1 by default. Over-budget requests ALWAYS pause for approval;
  re-runs after grant must proceed without asking twice
  (`_approval_granted`).
- Approval matrix: buyer <=50k · Branch Head 50k–5L · GM 5L–25L ·
  MD >25L.

## Failure behaviour
- Invalid payload → VALIDATION_ERROR, nothing written.
- Gateway fault → FAILED; idempotency key prevents a second PO on
  retry.

## Never
- Invent prices, vendors or items not in the request.
- Split an order to stay under an approval threshold.
