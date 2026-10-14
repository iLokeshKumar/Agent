# M1 Policy
## Approval Gates
| Condition | Gate |
|-----------|------|
| Daily ISM briefing generation | Auto-generate at 08:00 IST; no gate |
| DSO > 42 days | ISM notified immediately; ISM decides collection action |
| DSO > 60 days | Escalate to GSM (M2); ISM and GSM joint review required |
| Receivables data request for accounts > ₹10,00,000 outstanding | ISM must explicitly request; no auto-display |
| Report shared outside internal sales team | Business Head approval required |

## Constraints
1. The agent is advisory only; it cannot initiate collections, issue reminders, or modify CRM records.
2. DSO is computed daily using a consistent period of the current calendar month.
3. The ISM briefing must not include customer-level PII beyond account name and outstanding amount.
4. Pipeline stage definitions are sourced from crm_stage_master; the agent cannot redefine stages.
5. All briefings are versioned and retained for 90 days for performance review purposes.

## Data
- Source: accounts_receivable, sales_invoices, crm_opportunities, crm_stage_master, customer_master (read-only)
- Output: ism_briefings table, dso_kpi_log, ism_alert_queue
