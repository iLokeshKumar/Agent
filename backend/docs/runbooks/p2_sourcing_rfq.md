# P2 Sourcing RFQ — Agent Runbook

Suite: Purchase | Autonomy: A2

## Purpose
Sends RFQ emails to shortlisted vendors based on approved requisition.
Collects quotes and presents comparison to buyer. Does not select vendor
autonomously — that's a human decision.

## Normal operation
1. P1 sets `status = approved` and triggers P2
2. P2 identifies vendors from CRM contacts (category match)
3. Drafts RFQ email; routes for buyer approval before send (`mail_send_enabled`)
4. On approval, sends RFQ; logs to `message_log`
5. Inbound quotes are stored in `doc_index`; P2 compares and presents

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No vendors identified | CRM contact category not set | Tag vendors in Zoho CRM with product category |
| RFQ not sent | `mail_send_enabled=False` | Enable SMTP in settings or approve manual send |
| Quotes not parsed | PDF extraction low confidence | Human reviews `doc_index.extract_json` |
