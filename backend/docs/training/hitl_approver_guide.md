# Yexis AI Agent Platform — HITL Approver Guide

HITL = Human-in-the-Loop. You are a named approver for one or more
agent action types. This guide explains your responsibilities, the
approval interface, and what each approval type means.

---

## Your role

When an agent reaches an action it cannot take autonomously (A1 or A2
escalation), it pauses and sends you an approval request. Your response
unblocks the agent. Every approval is logged to the Action Ledger —
permanent, append-only record.

**Golden rule: if you're not sure, reject and ask IT.**

---

## How approvals are delivered

1. **WhatsApp**: You receive a formatted message with the action details
   and two options: *Approve* (reply `APPROVE`) or *Reject* (reply `REJECT <reason>`)
2. **Email**: Same content, with Approve / Reject links
3. **API** (for programmatic approvals):
   ```
   POST /approvals/{approval_id}/approve
   POST /approvals/{approval_id}/reject
   Body: {"reason": "optional note"}
   ```

---

## Approval types and what you are confirming

### P1 — Purchase Requisition (≥ your tier threshold)
You are confirming:
- The business need is genuine
- The vendor and price are appropriate
- Budget is available

**Do NOT approve if**: price is missing, vendor is unknown, or
the total exceeds your tier limit (escalate up).

---

### P2 — RFQ Send
You are confirming:
- The vendor list is correct
- The RFQ content is accurate
- It's OK to contact these vendors

**Do NOT approve if**: vendor is blacklisted or the RFQ contains
commercially sensitive pricing information that shouldn't be shared.

---

### F1 — Stop-Supply Recommendation
You are confirming:
- The customer is genuinely in breach
- Other resolution paths have been exhausted
- You accept responsibility for the stop-supply decision

**This is a serious action.** Only approve after reviewing the full
payment history in D1 or Zoho Books.

---

### F2 — Scheme Claim Submission
You are confirming:
- The claim amount is correct
- The supporting serials are genuine sales
- You authorise submission to Samsung portal

**Do NOT approve if**: serial numbers appear incorrect or the claim
period doesn't match the circular.

---

### V1 — Chargeable Service (cost estimate)
You are confirming the quoted cost estimate to the customer.
On approval, V1 sends the estimate to the customer for their acceptance.

---

### V2 — AMC Renewal Proposal
You are confirming:
- The asset and customer details are correct
- The renewal term and value are acceptable
- It's OK to create the new AMC contract

**Do NOT approve if**: the asset has been disposed of or the customer has
a pending dispute.

---

### F4 — Interentity Recon Open Item
You are confirming:
- The reconciliation item is a genuine cross-entity transaction
- The matched/unmatched status is correct
- Any proposed adjustments are authorised

---

### F5 — Cash Forecast Draw-down Proposal
You are confirming:
- The forecast horizon is appropriate (1–13 weeks)
- The proposed draw-down is within treasury policy
- You accept responsibility for the cash deployment decision

---

### H1 — Job Description (for posting)
You are confirming (as HR Manager):
- The JD is accurate and complete
- Compensation band is approved
- It's OK to post this role externally

**Always review**: the JD draft is AI-generated and marked "pending HR Manager
review" — it must not be posted without your sign-off.

---

### H2 — Onboarding Completion
You are confirming (as HR Manager):
- All onboarding items are genuinely completed
- The employee has received all mandatory briefings
- The employee record can be marked active

---

### H4 — Payroll Anomaly (HIGH severity)
You are confirming:
- You have investigated the flagged anomaly (ghost employee, duplicate, outlier gross)
- The anomaly has been resolved or is a known exception
- Payroll can proceed for the flagged employee

**Do NOT approve if**: you cannot identify the employee in the HR master or
the gross figure cannot be explained.

---

### S4 — Physical Count Reconciliation
You are confirming (as Store Manager):
- The count exercise covered all locations
- Discrepancies (missing, extra, mismatch) have been investigated
- The reconciled count can be accepted as the closing inventory position

---

### C1 — Indent Submission
You are confirming (as Buyer):
- The SKU weeks cover is genuinely below the reorder threshold
- The suggested quantity is appropriate
- The indent can be raised as a Requisition → P1

---

### C3 — SCM Claim Settlement
You are confirming (as Finance Manager):
- The settled amount is correct and matches the Samsung / logistics partner confirmation
- Supporting evidence is on file
- The claim can be closed at this amount

---

### K4 — Dealer Incentive Disbursement
You are confirming (as Finance Manager):
- The incentive eligibility computation is correct
- The dealer qualifies under the current scheme slab
- The amount can be disbursed / credited to the dealer account

---

## Response time expectations

| Tier | Expected response |
|---|---|
| Branch Head | Within 2 business hours |
| GM | Within 4 business hours |
| MD | Within 1 business day |

After 24 hours without response, the agent sends an escalation reminder.
After 48 hours, the task is flagged as stale and IT is notified.

---

## Viewing pending approvals

```
GET /approvals/pending?assigned_to=<your_role>
```

Or ask D1:
> "What approvals are pending for me?"

---

## Viewing the action ledger

Every action taken on your approval is in the Action Ledger:
```
GET /audit/ledger?approved_by=<your_role>&limit=50
```

This is an **append-only** log — no one can delete entries, including IT.

---

## Escalation path

If an approval needs to go to a higher tier, reply `ESCALATE`:
- Your response is logged as "escalated"
- The request is re-routed to the next tier
- You remain the originating approver in the audit log

---

## If you receive an unexpected approval request

1. Do NOT approve it
2. Reply `REJECT unexpected_request`
3. Contact IT immediately with the approval ID
4. The Action Ledger will show who triggered the request

---

## Common questions

**Q: Can I approve on behalf of someone else?**
A: No. Only the named approver for the tier can approve. Delegates must
   be configured in the platform settings by IT admin.

**Q: What if I approve something by mistake?**
A: Contact IT immediately. Some actions (e.g. RFQ sends) cannot be recalled
   once sent. The Action Ledger will reflect the error and your correction.

**Q: Can agents approve their own actions?**
A: No. The approval gate requires a human (non-agent) identity.
