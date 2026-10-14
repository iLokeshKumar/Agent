# P1 Purchase Intake — Agent Runbook

Suite: Purchase | Autonomy: A2

## Purpose
Validates and routes purchase requisitions. Checks budget, formats the
requisition, and routes to the correct approver tier. Creates the
`Requisition` record in the local DB.

## Approval tiers
| Total | Approver |
|---|---|
| ≤ ₹50,000 | Auto (within budget) |
| ₹50,001 – ₹5,00,000 | Branch Head |
| ₹5,00,001 – ₹25,00,000 | GM |
| > ₹25,00,000 | MD + Finance |

## Normal operation
1. Requester submits via API / WhatsApp with item, qty, vendor, price
2. P1 validates: non-zero qty, price ≥ 0, vendor non-empty
3. Budget check against `budget_limit` in request (platform policy gate handles org-level caps)
4. Routes for approval; on approval creates Zoho Books PO via MCP

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Requisition stuck in `new` | Approver hasn't responded | Escalate via email/WA reminder |
| PO not created after approval | Zoho API error | Check `action_ledger` for error; re-trigger P2 |
| Budget check always fails | Budget limit missing | Pass explicit `budget_limit` or set org-level cap |

## KPI target
Reorder cycle: ≤ 3 days from requisition to PO.
