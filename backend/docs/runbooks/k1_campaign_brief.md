# K1 Campaign Brief — Runbook

**Agent code:** K1
**Autonomy:** A1 (creates draft briefs only; Marketing Manager must approve before campaign proceeds)

---

## Normal operation

Entry point for all marketing campaigns. The Marketing Manager provides brief details and K1 creates a structured Campaign record in `draft` status. The campaign remains in draft until the Marketing Manager explicitly approves it, unlocking downstream agents K2 (audience), K3 (broadcast), K4 (dealer promo), and K5/K6 (tracking). No audience or message is sent while status = `draft`.

Trigger commands: `create`, `update`, `report`

---

## Key actions

Create a new campaign brief:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "k1_campaign_brief", "action": "create", "payload": {"campaign_name": "Festive Dhamaka Oct 2026", "objective": "dealer_activation", "channel": "whatsapp", "product_category": "AC", "start_date": "2026-10-01", "end_date": "2026-10-31", "budget_inr": 500000}}'
```

Update an existing campaign brief:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "k1_campaign_brief", "action": "update", "payload": {"campaign_id": "CMP-2026-0012", "budget_inr": 600000, "end_date": "2026-11-05"}}'
```

List campaigns and their current status:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "k1_campaign_brief", "action": "report", "payload": {"status": "draft", "month": "2026-10"}}'
```

---

## Interpreting the output

| Field | Meaning |
|---|---|
| `campaign_id` | Unique Campaign record ID |
| `campaign_name` | Human-readable campaign label |
| `objective` | Campaign goal (e.g., `dealer_activation`, `product_launch`) |
| `channel` | Outreach channel (`whatsapp`, `email`, `sms`) |
| `status` | `draft` (default) / `approved` / `active` / `closed` |
| `budget_inr` | Approved budget in INR |
| `approved_by` | Marketing Manager who approved (null while draft) |
| `approved_at` | Approval timestamp |

---

## Rollback

To prevent new campaign records from being created:
```bash
curl -X PATCH http://localhost:8000/lanes/k1_campaign_brief \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

Campaigns in `draft` status have no downstream effect. To cancel a draft campaign before approval, update its status to `cancelled` via the `update` action. An approved campaign can only be deactivated by the Marketing Manager through the approval workflow.

---

## Escalation

| Trigger | Notify |
|---|---|
| Campaign budget exceeds INR 10,00,000 | Marketing Manager + Finance Manager for budget sign-off |
| Draft campaign not approved within 5 business days of creation | Marketing Manager reminder |
| Campaign objective conflicts with an active ongoing campaign | Marketing Manager to resolve overlap |
