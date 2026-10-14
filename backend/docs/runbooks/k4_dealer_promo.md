# K4 Dealer Promo — Runbook

**Agent code:** K4  
**Autonomy:** A1 (propose only — incentive computation advisory; Finance approves disbursement)

---

## Normal operation

K4 computes promotional incentive eligibility for enrolled dealers based on
their `payment_behavior_score` in DealerMart. Incentive slabs: score ≥ 90 → 3%,
score ≥ 70 → 2%, score ≥ 50 → 1%, below 50 → ineligible.

---

## Computing incentives

```
POST /api/agents/run
{
  "agent_code": "K4",
  "payload": {
    "action": "compute",
    "campaign_id": "<uuid>",
    "entity": "YEXIS-EL"
  }
}
```

Returns per-dealer incentive amounts and total proposed incentive.
K2 audience must be built before running K4.

---

## Interpreting output

| Field | Meaning |
|---|---|
| `eligible_dealers` | Count of dealers with score ≥ 50 |
| `total_incentive_proposed` | Sum of all proposed incentives (₹) |
| `eligible[].incentive_rate_pct` | Slab rate applied (1–3%) |
| `eligible[].incentive_amount` | Proposed payout (₹) |

---

## Rollback

K4 writes nothing — output is in task result only. Disable lane to stop runs:
```
PATCH /lanes/k4_dealer_promo
{"dry_run": true}
```

---

## Escalation

- Total incentive > ₹5L → Finance Controller review before approval
- Dealer score discrepancy → DealerMart refresh needed (run nightly mart builder)
