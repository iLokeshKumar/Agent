# C2 Policy
## Approval Gates
| Condition | Gate |
|-----------|------|
| Shipment delivers within 3 days with valid POD | Auto-close; no gate |
| Shipment exceeds 3 days without POD | SCM Manager review and chase escalation |
| Delivery disputed by consignee | SCM Manager resolution required before close |
| POD reference format invalid | VALIDATION_ERROR; reject close attempt |
| Transporter unresponsive after 2 chases | SCM Manager must engage transporter directly |

## Constraints
1. POD recording is irreversible; disputes must be filed as separate exception records.
2. The daily dispatch-to-POD KPI report must be generated before 09:00 IST each day.
3. The agent cannot modify consignee or delivery address after dispatch registration.
4. Chase alerts must be templated; free-form messages to transporters are not permitted.
5. All escalations must include LR number, dispatch date, consignee name, and delay duration.

## Data
- Source: dispatch_register, logistics_partner_api, consignee_master
- Output: pod_records table, shipment_exceptions queue, dispatch_kpi_report
