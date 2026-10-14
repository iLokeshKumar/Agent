# K2 Audience Builder — Runbook

**Agent code:** K2
**Autonomy:** A2 (builds and loads audience autonomously once campaign is approved; clear requires Marketing Manager confirmation)

---

## Normal operation

Runs after K1 campaign approval. K2 queries DealerMart to build a targeted dealer contact list based on campaign criteria (territory, category, tier, payment behaviour score, etc.) and loads matches into CampaignContact records linked to the campaign. Once loading is complete, the agent sets the Campaign status to `audience_built`, signalling K3 that broadcast can proceed.

Trigger commands: `build`, `report`, `clear`

---

## Key actions

Build audience for an approved campaign:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "k2_audience_builder", "action": "build", "payload": {"campaign_id": "CMP-2026-0012", "filters": {"territory": ["SOUTH", "WEST"], "dealer_tier": ["A", "B"], "category_focus": "AC", "min_payment_score": 60}}}'
```

Report on audience built for a campaign:
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "k2_audience_builder", "action": "report", "payload": {"campaign_id": "CMP-2026-0012"}}'
```

Clear audience and reset campaign to approved (for rebuild):
```bash
curl -X POST http://localhost:8000/api/agents/run \
  -H "Content-Type: application/json" \
  -d '{"lane": "k2_audience_builder", "action": "clear", "payload": {"campaign_id": "CMP-2026-0012", "reason": "Filter criteria revised by Marketing Manager"}}'
```

---

## Interpreting the output

| Field | Meaning |
|---|---|
| `campaign_id` | Campaign being targeted |
| `contact_count` | Number of CampaignContact records created |
| `source_table` | `DealerMart` — primary source for dealer attributes |
| `filters_applied` | Echo of filter criteria used for audit |
| `campaign_status` | Updated to `audience_built` on successful load |
| `opted_out_excluded` | Count of dealers excluded due to opt-out flag |
| `duplicates_removed` | Deduplication count within the build run |

---

## Rollback

To pause audience building:
```bash
curl -X PATCH http://localhost:8000/lanes/k2_audience_builder \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

The `clear` action removes all CampaignContact records for the campaign and resets Campaign status from `audience_built` back to `approved`. This allows the audience to be rebuilt with revised filters without affecting the Campaign record itself. K3 will not send messages until status returns to `audience_built`.

---

## Escalation

| Trigger | Notify |
|---|---|
| Audience size < 50 contacts after filtering | Marketing Manager to review filter criteria |
| DealerMart data stale (last sync > 7 days) | IT/Systems team + Marketing Manager |
| Opted-out dealers > 20% of raw audience | Marketing Manager + Compliance review |
