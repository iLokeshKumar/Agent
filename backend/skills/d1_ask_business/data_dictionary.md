# D1 Ask-the-Business — Data Dictionary

Field definitions for all mart tables and KPI snapshots available to
ask-the-business sessions. Each field lists its source, unit, and the
natural-language question it answers.

---

## dealer_mart

| Field | Type | Source | Answers |
|---|---|---|---|
| `dealer_ref` | string | Zoho Books `customer_id` | "Which dealer?" |
| `branch` | string | CRM contact company_name | "Which branch?" |
| `outstanding_amt` | ₹ float | Sum of open invoice balances | "How much does dealer X owe?" |
| `overdue_buckets_json` | JSON | Ageing by: current / 1-30 / 31-60 / 61-90 / 90+ days | "What's the ageing breakdown?" |
| `payment_behavior_score` | 0–1 float | kept / (kept+broken) PromiseRecords | "Is this dealer reliable?" |
| `last_order_ts` | datetime | Latest invoice date | "When did they last order?" |
| `refreshed_ts` | datetime | Nightly mart run time | "How fresh is this data?" |

---

## sku_health

| Field | Type | Source | Answers |
|---|---|---|---|
| `sku_ref` | string | SerialRegistry `sku_ref` | "Which SKU?" |
| `branch` | string | SerialRegistry `location_ref` | "Which location?" |
| `period` | date | Nightly run date | "As of when?" |
| `on_hand` | integer | Count of in_stock+reserved serials | "How many units in stock?" |
| `weeks_cover` | float (null) | Not available until offtake feed | "How many weeks of stock?" |
| `ageing_buckets_json` | JSON | 0-30 / 31-60 / 61-90 / 90+ days since receipt | "How old is the stock?" |
| `eol_flag` | bool | Caller-supplied EOL SKU list | "Is this SKU end-of-life?" |
| `action_reco` | string | stock_health service verdict | "What should we do?" |

---

## vendor_scorecard

| Field | Type | Source | Answers |
|---|---|---|---|
| `vendor_ref` | string | VendorScorecardEvent `vendor_ref` | "Which vendor?" |
| `period` | date | Nightly run date | "As of when?" |
| `entity` | string | YEXIS-EL or YEXIS-SL | "Which entity?" |
| `total_events` | integer | All confirm+delay+deliver events | "How active is this vendor?" |
| `on_time_pct` | 0–1 float | deliver events where actual ≤ promised | "Do they deliver on time?" |
| `full_qty_pct` | 0–1 float | deliver events with zero qty_var | "Do they deliver full quantity?" |
| `otif_pct` | 0–1 float | avg(on_time, full_qty) | "What's their OTIF score?" |
| `avg_delay_days` | float | Mean(actual_dt − promised_dt) on delivers | "How many days late on average?" |

---

## installed_base

| Field | Type | Source | Answers |
|---|---|---|---|
| `asset_id` | string | = serial (globally unique) | "Which asset?" |
| `serial` | string | SerialRegistry | "What's the serial number?" |
| `customer_ref` | string | Invoice cache `customer_id` | "Who owns this?" |
| `product_ref` | string | SerialRegistry `sku_ref` | "What product?" |
| `sold_invoice_ref` | string | SerialRegistry | "What invoice was it sold on?" |
| `install_ts` | datetime | Last event time at sold status | "When was it installed?" |
| `warranty_end` | date | Set by V1/V2 on first service visit | "Is it under warranty?" |
| `amc_contract_ref` | string | V2 AMC contract ref | "Is there an AMC?" |
| `amc_end` | date | V2 contract expiry | "When does the AMC expire?" |
| `status` | string | active / expired / scrapped | "Is it active?" |
| `last_service_ts` | datetime | V1 service desk | "When was it last serviced?" |
| `fault_count_12m` | integer | V1 service desk | "How fault-prone is this asset?" |

---

## amc_contract

| Field | Type | Source | Answers |
|---|---|---|---|
| `contract_ref` | string | V2 auto-generated or provided | "What's the contract ID?" |
| `asset_id` | string | InstalledBase asset_id (= serial) | "Which asset is covered?" |
| `customer_ref` | string | Customer contact ref | "Who holds the AMC?" |
| `entity` | string | YEXIS-EL or YEXIS-SL | "Which entity?" |
| `start_date` | date | Contract start | "When did the AMC start?" |
| `end_date` | date | Contract end | "When does the AMC expire?" |
| `value` | ₹ float | Annual contract value | "What does the AMC cost?" |
| `status` | string | active / expired / renewed / cancelled | "Is the AMC current?" |
| `renewal_ref` | string | Linked renewal contract_ref | "Has it been renewed?" |

---

## dispatch_record

| Field | Type | Source | Answers |
|---|---|---|---|
| `dispatch_ref` | string | Invoice/waybill ref | "Which dispatch?" |
| `dispatch_type` | string | inbound / outbound | "Samsung→Yexis or Yexis→Dealer?" |
| `from_ref` | string | Vendor or branch | "Where was it dispatched from?" |
| `to_ref` | string | Branch or dealer | "Where is it going?" |
| `qty` | integer | Units dispatched | "How many units?" |
| `status` | string | dispatched / in_transit / delivered / delayed / exception | "What's the status?" |
| `dispatch_ts` | datetime | Time of dispatch | "When was it dispatched?" |
| `expected_delivery` | date | Agreed delivery date | "When should it arrive?" |
| `pod_ts` | datetime | Proof-of-delivery time | "When was it delivered?" |

---

## scm_claim

| Field | Type | Source | Answers |
|---|---|---|---|
| `claim_ref` | string | C3 auto or manual | "Which claim?" |
| `claim_type` | string | short_supply / damage / price_diff / scheme / logistics | "What kind of claim?" |
| `counterparty_ref` | string | Samsung / logistics partner | "Who is the claim against?" |
| `amount_claimed` | ₹ float | Claimed amount | "How much are we claiming?" |
| `amount_settled` | ₹ float | Settled amount | "How much was recovered?" |
| `status` | string | open / submitted / acknowledged / settled / rejected | "Where is the claim?" |
| `raised_ts` | datetime | When C3 created the claim | "When was it raised?" |
| `settled_ts` | datetime | When it was closed | "When was it settled?" |

---

## campaign

| Field | Type | Source | Answers |
|---|---|---|---|
| `campaign_ref` | string | K1 auto or manual | "Which campaign?" |
| `name` | string | Campaign name | "What's it called?" |
| `campaign_type` | string | dealer_promo / end_customer / scheme_push / event | "What type of campaign?" |
| `status` | string | draft / audience_built / broadcast / tracking / closed | "Where is it in the funnel?" |
| `kpi_json` | JSON | K6 computed analytics | "What were the results?" |
| `kpi_json.delivery_rate_pct` | % | sent / delivered × 100 | "How many messages were delivered?" |
| `kpi_json.response_rate_pct` | % | responded / delivered × 100 | "How many responded?" |
| `kpi_json.conversion_rate_pct` | % | converted / delivered × 100 | "How many converted?" |
| `kpi_json.hot_lead_rate_pct` | % | hot_leads / delivered × 100 | "How many hot leads?" |

---

## hr_employee (attrition risk)

| Field | Type | Source | Answers |
|---|---|---|---|
| `employee_id` | string | HR master | "Which employee?" |
| `name` | string | HR master | "Name?" |
| `department` | string | HR master | "Which department?" |
| `risk_score` | 0–100 float | H3 scoring model | "What's the attrition risk?" |
| `risk_tier` | High / Medium / Low | Derived: ≥60 High, 30-59 Medium, <30 Low | "Is this employee high risk?" |
| `last_engagement_ts` | datetime | H3 signal input | "When were they last engaged?" |

---

## kpi_snapshot

| `kpi_id` | Unit | Formula | Owner |
|---|---|---|---|
| `dso_days` | days | avg(today − invoice_date) on settled AR | F1 |
| `p4_auto_match_rate` | 0–1 | FULLY_MATCHED / invoice_matching tasks | P4 |
| `promise_reliability` | 0–1 | kept / (kept+broken) promises | F1 |
| `overdue_receivables` | ₹ | sum(balance) where due < today | F1 |
| `total_open_receivables` | ₹ | sum(all open invoice balances) | F1 |
| `f3_books_tally_drift_hrs` | ₹ | sum(abs drift) on unresolved TB rows | F3 |
| `f2_claim_accuracy_pct` | % | approved / (approved+rejected) claims | F2 |
| `cash_forecast_accuracy` | % | kept promises this month / made this month | F1 |
| `stock_availability_pct` | % | (in_stock+reserved) / active serials | S2 |
| `serial_conservation_rate` | % | (total − mystery) / total serials | S1 |
| `reorder_cycle_days` | days | avg(today − req.created_ts) for po_created | P1 |
| `vendor_otif_pct` | % | weighted avg vendor otif_pct | P3 |
| `dispatch_to_pod_days` | days | avg(pod_ts − dispatch_ts).days on C2 delivered | C2 |
| `requisition_cycle_hours` | hours | same as reorder_cycle_days in hours | P1 |
| `time_to_fill_days` | days | avg(updated − created) on hire tasks | H1 |
| `amc_renewal_rate_pct` | % | renewed / expiring_in_90d × 100 per entity | V2 |
| `revenue_per_fte` | ₹ | total_open / headcount | D1 |
| `attrition_risk_score` | 0–100 | mean H3 risk_score across active employees | H3 |
| `talk_time_pct` | % | outbound WA / total WA messages | M1 |
| `pipeline_coverage_ratio` | null | reserved (live CRM data not yet wired) | K1 |
| `scm_claim_resolution_days` | days | avg(settled_ts − raised_ts).days on C3 settled | C3 |
| `campaign_delivery_rate_pct` | % | avg delivery_rate_pct across closed campaigns | K6 |
| `campaign_response_rate_pct` | % | avg response_rate_pct across closed campaigns | K6 |

---

## Natural-language query routing

| User says | Route to | Read tool |
|---|---|---|
| "What do we owe / are owed?" | dealer_mart | `read_dealer_outstanding` |
| "Which dealers are overdue?" | dealer_mart | `read_overdue_dealers` |
| "How is dealer X performing?" | dealer_mart | `read_dealer_card` |
| "Stock position for SKU Y?" | sku_health | `read_sku_position` |
| "Which SKUs need action?" | sku_health | `read_sku_actions` |
| "How is vendor Z performing?" | vendor_scorecard | `read_vendor_card` |
| "OTIF score this month?" | vendor_scorecard / kpi_snapshot | `read_kpi` |
| "Assets installed at site S?" | installed_base | `read_installed_base` |
| "AMC contracts expiring soon?" | installed_base | `read_amc_expiry` |
| "Show me active AMC contracts?" | amc_contract | `read_amc_contracts` |
| "Which dispatches are overdue?" | dispatch_record | `read_dispatch_status` |
| "What SCM claims are open?" | scm_claim | `read_scm_claims` |
| "Campaign results for Sep?" | campaign | `read_campaign_summary` |
| "Who is high attrition risk?" | hr_employee | `read_attrition_risk` |
| "What is our DSO?" | kpi_snapshot | `read_kpi` |
| "Branch scorecard?" | kpi_snapshot | `read_branch_kpis` |
| "Campaign delivery rate?" | kpi_snapshot | `read_kpi` (campaign_delivery_rate_pct) |
| "Attrition risk score?" | kpi_snapshot | `read_kpi` (attrition_risk_score) |
| "SCM claim resolution time?" | kpi_snapshot | `read_kpi` (scm_claim_resolution_days) |
