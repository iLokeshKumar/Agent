# Campaign Brief — K1
**Version:** 1.0.0  
**Autonomy:** A1  
**Go-live:** 2026-09-11  
**Primary KPI:** `campaign_count`  
**KPI Target:** N/A

## Purpose
Captures and structures marketing campaign briefs submitted by the Marketing team. Converts free-form campaign requirements into a structured brief record with objectives, target segments, channels, budget, and timelines. All briefs require Marketing Manager approval before downstream campaign agents are triggered.

## Trigger
Triggered when a marketing team member submits a new campaign brief via the operations dashboard or WhatsApp bot.

## Actions
| Action | Description | Autonomy |
|--------|-------------|----------|
| parse_brief_input | Extract structured fields from free-form brief submission | A1 |
| validate_brief | Check mandatory fields: objective, segment, channel, budget, start_date | A1 |
| create_brief_record | Persist the structured brief to the campaigns table | A1 |
| route_for_approval | Submit brief to Marketing Manager approval queue | A1 (human gate) |
| notify_submitter | Confirm brief receipt and approval status to submitter | A1 |

## Constraints
- Never activate a campaign without Marketing Manager approval of the brief.
- Never create a brief without a defined objective and target segment.
- Budget fields must be validated against the active marketing budget envelope.
- Briefs cannot reference product SKUs that are discontinued or unlaunched.
- All brief records are immutable after approval; amendments require a new brief version.
- Optional model-generated message concepts run in shadow mode only. They are not saved as approved copy, and must never be sent, scheduled, or used to bypass the existing approval gates.
