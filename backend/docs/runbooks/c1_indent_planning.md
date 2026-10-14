# C1 Indent Planning — Runbook

**Agent code:** C1
**Autonomy:** A1 (proposes only; SCM Manager must approve before any indent is submitted)

---

## Normal operation

Runs weekly (or on-demand) to analyse SKU-level stock health and compute replenishment indents targeting a 12-week cover. The agent plans indent quantities using SkuHealth data (current stock, average weekly sales, lead time), generates a draft indent, and submits it for SCM Manager approval. No indent is dispatched to the supplier until explicit approval is received (`NEEDS_APPROVAL` gate).

Trigger commands: `plan`, `submit`, `report`

---

## Key actions

Run indent planning across all SKUs:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "c1_indent_planning", "action": "plan", "payload": {"target_weeks_cover": 12, "category": "ALL"}}'
```

Submit a draft indent for SCM Manager approval:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "c1_indent_planning", "action": "submit", "payload": {"indent_id": "IND-2026-0045", "notes": "Pre-festive buffer build"}}'
```

Generate SKU health and coverage report:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "c1_indent_planning", "action": "report", "payload": {"filter": "below_cover", "weeks_threshold": 8}}'
```

---

## Interpreting the output

| Field | Meaning |
|---|---|
| `indent_id` | Draft indent reference |
| `sku_code` | Product SKU from SkuHealth |
| `current_stock` | Units on hand at time of planning run |
| `avg_weekly_sales` | 13-week rolling average sales velocity |
| `weeks_cover` | `current_stock / avg_weekly_sales`; target = 12 |
| `indent_qty` | Recommended order quantity to reach 12-week cover |
| `status` | `draft` / `NEEDS_APPROVAL` / `approved` / `submitted_to_supplier` |
| `below_cover_count` | Number of SKUs below the target weeks cover |
| `approval_ref` | Token issued on submit; required for SCM Manager to approve |

---

## Rollback

To freeze planning and prevent new indents from being raised:
```bash
curl -X PATCH http://localhost:8000/lanes/c1_indent_planning \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

Draft indents in `NEEDS_APPROVAL` state are never forwarded to suppliers without SCM Manager sign-off. To withdraw a submitted draft:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "c1_indent_planning", "action": "withdraw", "payload": {"indent_id": "IND-2026-0045"}}'
```

---

## Escalation

| Trigger | Notify |
|---|---|
| More than 10 SKUs below 4-week cover | SCM Manager immediately |
| Indent approval pending for > 72 hours | SCM Manager + GM |
| SkuHealth data stale (last updated > 48 hours ago) | IT/Systems team + SCM Manager |
