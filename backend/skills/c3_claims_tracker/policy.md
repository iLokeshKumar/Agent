# C3 Policy
## Approval Gates
| Condition | Gate |
|-----------|------|
| Claim fully accepted by counterparty at claimed value | Auto-close after SCM Manager ratification |
| Credit note differs from claimed value | SCM Manager approval required before acceptance |
| Claim rejected by counterparty | SCM Manager decides next action (escalate/write-off) |
| Claim age > 25 days without resolution | SCM Manager intervention gate |
| Claim value > ₹1,00,000 | SCM Manager sign-off required at each status transition |

## Constraints
1. Claims must have at least one evidence document attached at registration; undocumented claims are rejected.
2. The agent cannot write off a claim; write-off requires explicit SCM Manager and Finance Manager approval.
3. Vendor credit notes must be validated against the original invoice before acceptance.
4. The 30-day resolution KPI is measured from claim registration date; clock does not pause for counterparty delays.
5. All claim notices and responses must be archived in the document management system.

## Data
- Source: claims_register, purchase_invoices, grn_records, vendor_master, logistics_partner_master
- Output: claims_status_table, credit_note_register, scm_exceptions_queue, claims_kpi_report
