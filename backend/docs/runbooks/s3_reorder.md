# S3 Reorder — Agent Runbook

Suite: Stores | Autonomy: A2

## Purpose
Auto-generates purchase requisitions when stock falls below reorder point.
Reorder point is set per SKU per branch. Requisition creation is A2
(supervised) — the buyer confirms before PO creation.

## Normal operation
1. S3 runs after `sku_health` refresh (nightly or on demand)
2. For each SKU+branch: if `on_hand < reorder_point`, creates `Requisition`
3. Reorder qty = `max_stock - on_hand` (configurable per SKU)
4. Routes requisition to buyer for confirmation
5. On confirmation, P1 takes over the approval chain

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Excess requisitions created | Reorder point set too high | Update reorder params in SKU config |
| No requisitions for low stock | Reorder point not set | Set reorder_point in SKU configuration table |
| KPI `reorder_cycle_days` high | Requisitions stuck in approval | Escalate; check `requisition.status = 'approved'` count |
