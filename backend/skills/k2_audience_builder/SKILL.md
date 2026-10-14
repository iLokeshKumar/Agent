# Audience Builder — K2
**Version:** 1.0.0  
**Autonomy:** A2  
**Go-live:** 2026-09-11  
**Primary KPI:** `audience_size`  
**KPI Target:** N/A

## Purpose
Builds targeted contact lists (audiences) for marketing campaigns by querying the customer and lead databases against segment criteria defined in the approved campaign brief. Produces a deduplicated, opted-in audience list ready for broadcast by the K3 agent.

## Trigger
Triggered automatically when a campaign brief is approved (K1 output), or on-demand by the Marketing Manager to preview audience size before brief approval.

## Actions
| Action | Description | Autonomy |
|--------|-------------|----------|
| parse_segment_criteria | Extract filter conditions from the approved campaign brief | A2 |
| query_customer_db | Run segment query against customer_master and leads tables | A2 |
| apply_opt_in_filter | Remove contacts without valid marketing consent | A2 |
| deduplicate_list | Remove duplicate contacts across channels | A2 |
| persist_audience | Save final audience list linked to campaign_id | A2 |
| notify_marketing | Report audience size and segment breakdown to Marketing Manager | A2 |

## Constraints
- Never include contacts who have opted out of marketing communications.
- Never export raw customer PII outside the platform without Marketing Manager approval.
- Audience lists must be regenerated fresh for each campaign run; stale lists are not reused.
- Queries must not exceed 24-hour runtime; large segments must be chunked.
- All audience builds must be audit-logged with query parameters and record count.
