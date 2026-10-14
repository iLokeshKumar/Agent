# V1 Service Desk — Agent Runbook

Suite: Service | Autonomy: A2

## Purpose
First-line service intake for Samsung product complaints, warranty claims,
and AMC service requests. Logs service visits to `installed_base`.
Routes complex faults to field engineers.

## Normal operation
1. Customer contacts via WhatsApp or email
2. V1 identifies asset from serial number (looks up `installed_base`)
3. Classifies: warranty / AMC / chargeable
4. For warranty/AMC: creates service ticket and acknowledges customer
5. Updates `installed_base.last_service_ts` and `fault_count_12m`
6. Routes chargeable cases to field engineer with cost estimate for approval

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Asset not found | Serial not in `installed_base` | Check `serial_registry` (may not have been sold yet) |
| Warranty status incorrect | `warranty_end` not set | Update `installed_base.warranty_end` from invoice |
| Service visit not logged | V1 task failed | Check `action_ledger` for error |
| Customer getting no response | WA template not approved | Check `template_registry.approval_status` |

## AMC handoff
When AMC contract is found (`amc_contract_ref` set), V1 hands off to V2
AMC Engine for renewal processing.
