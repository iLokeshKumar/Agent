# Yexis AI Agent Platform — Business User Guide

Welcome to the Yexis AI Agent Platform. This guide explains what the
agents do, how to interact with them, and what to expect.

---

## What the platform does

The platform runs 31 AI agents that automate routine tasks across Finance,
Purchase, Stores, SCM, Service, HR, Campaigns, and Sales command. Agents
work in three modes:

| Mode | What it means | Example |
|---|---|---|
| **A1 Propose-only** | Agent drafts; human approves before any action | C1 indent suggestions |
| **A2 Supervised** | Agent acts within defined bounds; you can override | F1 payment reminders |
| **A3 Autonomous** | Agent acts fully within policy; you review logs | K3 WhatsApp broadcast |

---

## Agent directory

### Finance (F1–F5)

| Agent | What it does | Who acts |
|---|---|---|
| **F1 Collections** | Sends overdue payment reminders; escalates to stop-supply proposal | A/R Team |
| **F2 Scheme Claims** | Validates and submits Samsung scheme claims | Finance Manager |
| **F3 Books→Tally** | Stages Zoho Books postings for Tally — nothing posts without accountant approval | Accountant |
| **F4 Interentity Recon** | Reconciles interentity balances (YEXIS-EL ↔ YEXIS-SL) | Finance Manager |
| **F5 Cash Forecast** | 13-week rolling cash forecast from receivables + payables | Finance Manager |

### Purchase (P1–P4)

| Agent | What it does | Who acts |
|---|---|---|
| **P1 Purchase Intake** | Validates and routes purchase requisitions to the right approver | Buyer / Branch Head |
| **P2 Sourcing RFQ** | Issues RFQs to vendors, normalises quotes, recommends award | Buyer |
| **P3 Expediting** | Chases vendors on open POs; alerts on slippage and impact | Purchase Team |
| **P4 Invoice Matching** | Three-way match (Invoice + PO + GRN) — auto-approves exact matches | Accounts Payable |

### Stores (S1–S4)

| Agent | What it does | Who acts |
|---|---|---|
| **S1 GRN Serialisation** | Captures serial numbers at goods receipt; writes to serial registry | Stores Team |
| **S2 Stock Health** | Weekly ageing and EOL scoring of stock; feeds S3 | Stores Manager |
| **S3 Reorder** | Deterministic reorder-point proposals → triggers P1 | Stores Manager |
| **S4 Physical Verification** | Serial-level physical count reconciliation against registry | Store Manager |

### SCM (C1–C3)

| Agent | What it does | Who acts |
|---|---|---|
| **C1 Indent Planning** | Demand-driven indent suggestions based on weeks cover | Purchase / SCM Team |
| **C2 Dispatch Tracker** | Records Samsung→Yexis and Yexis→Dealer dispatches; tracks POD | Logistics Team |
| **C3 SCM Claims Tracker** | Manages SCM-level claims (short supply, damage, price diff) | SCM Team |

### Service (V1–V2)

| Agent | What it does | Who acts |
|---|---|---|
| **V1 Service Desk** | Customer service intake; checks warranty/AMC; routes to engineer | Service Team |
| **V2 AMC Engine** | AMC contract expiry alerts; generates renewal proposals | Service Manager |

### HR (H1–H4)

| Agent | What it does | Who acts |
|---|---|---|
| **H1 Recruitment** | Job opening intake; drafts JD for HR Manager review | HR Manager |
| **H2 Onboarding** | Creates and tracks employee onboarding checklist | HR Team |
| **H3 Attrition Risk** | Scores each employee's attrition risk (tenure + engagement signals) | HR Manager |
| **H4 Payroll Check** | Pre-payment payroll anomaly detection: ghost employees, duplicates, outliers | HR Manager / Finance |

### Campaigns (K1–K6)

| Agent | What it does | Who acts |
|---|---|---|
| **K1 Campaign Brief** | Creates the campaign record and brief for the K-suite | Marketing Team |
| **K2 Audience Builder** | Segments dealer audience by branch and payment score | Marketing Team |
| **K3 Broadcast** | Sends WhatsApp messages via meta-approved templates (A3) | Automated — review logs |
| **K4 Dealer Promo** | Computes dealer incentive eligibility from outstanding and behavior | Marketing / Finance |
| **K5 Response Tracker** | Classifies inbound campaign responses; surfaces hot leads | Sales Team |
| **K6 Campaign Analytics** | Computes delivery, response, and conversion KPIs per campaign | Marketing Team |

### Sales Command (M1–M2)

| Agent | What it does | Who acts |
|---|---|---|
| **M1 ISM Command** | ISM pipeline, visit plan, targets, and alerts (read-only) | ISM |
| **M2 GSM Command** | GSM territory scorecard, KPI dashboard, and executive topline (read-only) | GSM / GM |

### Dashboard (D1)

**D1 Ask-the-Business** is your analytics assistant. Ask it business questions in plain English.

---

## How to ask the platform questions (D1)

### Via WhatsApp
Send a message to the platform number:
> "What is our total outstanding receivable?"
> "Which dealers are overdue by more than 60 days?"
> "What is the DSO this month?"
> "Stock position for QLED 55?"
> "Show me the campaign response rate for September"
> "What is the attrition risk score for YEXIS-EL?"

### Via the web API (for IT/admin)
```
POST /agents/d1/run
{"query": "Overdue dealers above 60 days", "entity": "YEXIS-EL"}
```

### What D1 can answer
- AR outstanding and ageing by dealer and branch
- Stock position and ageing by SKU
- Vendor OTIF scores
- Any of the 23 KPIs (see `skills/d1_ask_business/data_dictionary.md`)
- Installed base and AMC expiry queries
- Campaign delivery and response rates
- Attrition risk scores by entity

### What D1 cannot do
- D1 cannot create invoices, POs, or payments (it's read-only)
- For real-time Zoho data, ask the relevant agent (F1, P1, etc.)
- Data is refreshed nightly; for same-day data use Zoho Books directly

---

## Payment reminders (F1 Collections)

F1 sends automated payment reminders to overdue customers.

**When will a customer receive a reminder?**
- Day 1–30 overdue: WhatsApp reminder, gentle tone
- Day 31–60: WhatsApp + email, firmer tone
- Day 61–90: Email, formal tone (pending your review)
- 90+ days: Proposal to you only — you send it

**How to put an account on hold (dispute)**
Contact admin to add an `ObjectLock` for the account. F1 will skip it.

**How to record a payment promise**
If a customer promises to pay by a date, tell the platform:
> "Customer ACME promised ₹50,000 by 15 Sep"
The platform logs it as a PromiseRecord and tracks whether it's kept.

---

## Purchase requisitions (P1)

To raise a purchase requisition:
1. Send request: item, quantity, preferred vendor, budget
2. P1 validates and routes to the appropriate approver
3. You'll receive an approval request if it falls in your tier
4. On approval, P2 sends RFQ; P3 tracks delivery

**Approval tiers**
| Total | Who approves |
|---|---|
| ≤ ₹50,000 | Auto-approved (within budget) |
| ₹50,001 – ₹5 L | Branch Head |
| ₹5 L – ₹25 L | GM |
| > ₹25 L | MD + Finance |

---

## WhatsApp campaigns (K1–K6 suite)

The campaign suite automates dealer and customer outreach:

1. **K1** — Marketing creates a campaign brief (name, type, SKUs, dates)
2. **K2** — Platform builds the audience from DealerMart (branch + payment score filter)
3. **K3** — Platform broadcasts WhatsApp messages using meta-approved templates
4. **K5** — Responses are classified; hot leads (interested / callback / converted) are surfaced to Sales
5. **K6** — Campaign KPIs (delivery rate, response rate, conversion) are computed and stored

**You can only broadcast with a meta-approved template.** Contact IT to register new templates.

---

## AMC renewals (V2 AMC Engine)

V2 automatically monitors AMC contract expiry:
- Contracts expiring within 90 days appear in the renewal queue
- V2 generates a renewal proposal (new contract, same asset, +1 year)
- Service Manager approves → contract is created in the platform
- Contracts more than 30 days past expiry are marked expired and escalated

---

## Service requests (V1)

To log a service request for a customer:
1. Customer sends serial number + complaint via WhatsApp
2. V1 looks up the asset, checks warranty/AMC status
3. If under warranty/AMC: ticket is created and engineer is dispatched
4. If chargeable: V1 proposes a cost estimate — you approve before committing

---

## SCM claims (C3)

To raise a claim against Samsung or a logistics partner:
1. C3 intake: provide claim type, counterparty, invoice ref, amount
2. C3 validates and creates the claim record
3. Status flows: open → submitted → acknowledged → settled/rejected
4. Settlement always requires Finance Manager approval

---

## What you will receive automatically

| What | When | Channel |
|---|---|---|
| Morning digest (AR, DSO, top overdue) | Daily 08:00 | WhatsApp + Email |
| Branch scorecard (all KPIs) | Every Monday 08:00 | WhatsApp + Email |
| AMC expiry alerts (90-day window) | Daily 08:00 | WhatsApp |
| Payroll anomaly report | Before each payroll run | Email |
| Campaign response summary | Daily during active campaigns | WhatsApp |
| Approval requests | As they arise | WhatsApp + Email |
| Exception alerts | As they arise | WhatsApp |

---

## If something looks wrong

- **Wrong data in D1**: Data is from last night's nightly run. If it's more
  than 26 hours old, contact IT.
- **Reminder sent to wrong account**: Contact admin to add ObjectLock.
- **Approval not received**: Check WhatsApp and email; contact IT if missing
  after 30 minutes.
- **Campaign message sent to wrong contact**: K3 only sends to opt-in contacts
  with a valid MSISDN. Contact IT immediately with the campaign ref.
- **Agent taking an action you didn't expect**: Every action is logged to the
  Action Ledger. Contact IT immediately — actions can be reviewed and some
  can be reversed.

---

## Getting help

- Platform support: contact IT admin
- Zoho Books questions: your Zoho admin
- Tally questions: your accounts team
