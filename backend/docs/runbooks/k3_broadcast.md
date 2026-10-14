# K3 Broadcast — Runbook

**Agent code:** K3  
**Autonomy:** A3 (bounded-autonomous — sends only Meta-approved templates to opt-in contacts within batch cap)

---

## Prerequisites

Before K3 can run:
1. K1 campaign created (`status=draft`)
2. Marketing Manager approved campaign brief
3. K2 audience built (`status=audience_built`)
4. WhatsApp template registered and Meta-approved in `template_registry`

---

## Sending a broadcast

```
POST /api/agents/run
{
  "agent_code": "K3",
  "payload": {
    "action": "send",
    "campaign_id": "<uuid>",
    "template_id": "dealer_promo_v1"
  }
}
```

Sends to up to 200 opt-in contacts per call. Re-run to continue batches.
Sets campaign `status=broadcast` after first send.

---

## Delivery status

```
POST /api/agents/run
{
  "agent_code": "K3",
  "payload": {
    "action": "report",
    "campaign_id": "<uuid>"
  }
}
```

Returns: `total_audience`, `sent`, `pending`, `responded`.

---

## Hard guards

- Only Meta-approved templates (`approval_status = 'meta'`) may be broadcast
- Contacts with `opt_in_status = 'opt_out'` are skipped automatically
- Contacts without a known `msisdn` are skipped
- Max 200 per call — re-trigger for remaining batches

---

## Rollback

```
PATCH /lanes/k3_broadcast
{"dry_run": true}
```

Messages already sent cannot be recalled — disable lane to stop further sends.
Review `message_log` for sent records.

---

## Escalation

- Delivery failures > 10% → Marketing Manager + WABA provider support
- Any opt-out spike → pause campaign, review template content
