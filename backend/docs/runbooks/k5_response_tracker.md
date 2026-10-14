# K5 Response Tracker — Runbook

**Agent code:** K5  
**Autonomy:** A2 (supervised — keyword classification automatic; lead escalation requires Sales Manager)

---

## Recording a response

When a dealer or customer replies to a campaign broadcast:

```
POST /api/agents/run
{
  "agent_code": "K5",
  "payload": {
    "action": "record",
    "campaign_id": "<uuid>",
    "contact_ref": "DLR-0042",
    "response_type": "interested",
    "notes": "Dealer asked for Samsung QD-OLED demo unit availability."
  }
}
```

Valid `response_type`: `interested`, `not_interested`, `callback`, `converted`, `no_response`

Omit `response_type` and include `response_text` to use auto-classification.

---

## Auto-classifying unclassified responses

```
POST /api/agents/run
{
  "agent_code": "K5",
  "payload": {
    "action": "classify",
    "campaign_id": "<uuid>"
  }
}
```

Applies keyword matching to contacts with `response_ts` but no `response_type`.

---

## Response report

```
POST /api/agents/run
{
  "agent_code": "K5",
  "payload": {
    "action": "report",
    "campaign_id": "<uuid>"
  }
}
```

Returns response breakdown and `hot_leads` list (interested + callback + converted).

---

## Escalation

- `interested` or `callback` responses → notify assigned ISM within 24 hours
- `converted` responses → Sales Manager confirmation + F1/S3 downstream trigger
- `not_interested` / `unsubscribe` → update WaContact opt-out status

---

## Rollback

```
PATCH /lanes/k5_response_tracker
{"dry_run": true}
```

Responses already recorded can be corrected via `action=record` with updated `response_type`.
