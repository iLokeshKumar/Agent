# C1 Policy
## Approval Gates
| Condition | Gate |
|-----------|------|
| All indents (any value) | SCM Manager approval required before issuance |
| Indent value > ₹5,00,000 | Additional Finance Manager countersign required |
| New vendor not in master | Vendor onboarding must complete before indent raised |
| Reorder qty exceeds 180-day cover | SCM Manager must justify before approval |
| Emergency indent (urgency_flag=HIGH) | SCM Manager must approve within 4 hours |

## Constraints
1. The agent produces draft indents only; it has no authority to issue or transmit to vendors.
2. Reorder points are read from the inventory_config table; the agent cannot modify them.
3. Indent history must be preserved; approved, rejected, and cancelled indents are all logged.
4. SKUs flagged as discontinued must not generate indents; alert SCM Manager instead.
5. The agent must report reorder_cycle_days daily; a value > 3 triggers an automated escalation.

## Data
- Source: inventory_on_hand, inventory_config (reorder points), vendor_master, demand_forecast
- Output: purchase_indents table, indent_approval_queue, scm_alerts
