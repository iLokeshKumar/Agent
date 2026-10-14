# F4 Interentity Reconciliation — Runbook

**Agent code:** F4  
**Autonomy:** A2 (suggestions only — human confirms matches)

---

## Normal operation

F4 runs nightly at 02:00 alongside the standard mart chain. It scans
`InterentityOpenItem` rows with `match_status=open` for both YEXIS-EL↔YEXIS-SL
legs and fingerprints matches by `doc_type + amount (±0.1%) + doc_date (±5 days)`.

Clear single-pair matches are marked `suggested`. Ambiguous multi-candidate
items are surfaced for human pick. No item is ever set to `matched` automatically.

**Manual on-demand run:**
```
POST /api/agents/run
{
  "agent_code": "F4",
  "payload": {
    "entity_from": "YEXIS-EL",
    "entity_to": "YEXIS-SL",
    "action": "match"
  }
}
```

---

## Viewing suggestions

```
POST /api/agents/run
{
  "agent_code": "F4",
  "payload": {
    "entity_from": "YEXIS-EL",
    "entity_to": "YEXIS-SL",
    "action": "report"
  }
}
```

---

## Confirming a match

Review `suggestions` list from the `match` or `report` output.
For each `confidence=high` pair, Finance Controller approves via:

```
POST /api/approvals/{approval_id}/approve
{"note": "Confirmed YEXIS-EL INV-XXX ↔ YEXIS-SL INV-YYY"}
```

After approval, the platform sets `match_status=matched` on both legs.

---

## Adding open items

InterentityOpenItem rows are created by F3 (Books→Tally sync) and manual
Finance entry. They are never created by F4.

---

## Rollback

1. Set lane dry_run=true:
   ```
   PATCH /lanes/f4_interentity_recon
   {"dry_run": true}
   ```
2. Reset all suggested items back to open:
   ```sql
   UPDATE interentity_open_item
   SET match_status = 'open', suggestion_json = NULL
   WHERE match_status = 'suggested';
   ```
3. Investigate root cause before re-enabling.

---

## Escalation

- Ambiguous items >30 days old → Finance Controller immediate review
- Any `match_status=disputed` item → Finance Controller + MD notification
