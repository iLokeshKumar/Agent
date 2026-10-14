# Policy: S1 GRN & Serialisation

## Receipt rules
| Situation | Action |
|---|---|
| qty <= ordered (cumulative) | accept |
| cumulative > ordered | VALIDATION_ERROR, nothing written |
| unexpected extra item | OVER_RECEIVED, record + flag |
| duplicate serial | DUPLICATE_SERIAL flag, receipt continues |

## Status derivation
- COMPLETE: all lines fully received
- PARTIAL: some received
- OVER_RECEIVED: extras present

The PO's own status is derived downstream from cumulative receipts —
never edited by this agent directly.
