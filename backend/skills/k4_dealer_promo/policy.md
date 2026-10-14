# K4 Policy
## Approval Gates
| Condition | Gate |
|-----------|------|
| All dealer promo schemes | Marketing Manager approval required before communication |
| Scheme value > ₹5,000 per dealer unit | Business Head countersign required |
| Scheme conflicts with active manufacturer promotion | Block scheme; escalate to Brand Manager |
| Promo conversion rate < 10% after 14 days | Marketing Manager scheme review gate |
| Scheme eligibility criteria changed post-approval | Re-approval required; affected dealers notified |

## Constraints
1. The agent produces scheme designs and participation tracking only; dealer communication is handled by K3.
2. All scheme designs must reference a source brief from K1 and be linked to a campaign ID.
3. Trade margins used in incentive calculations must be sourced from the brand_agreement_master table.
4. Dealer eligibility is determined at scheme launch; mid-scheme eligibility changes require re-approval.
5. All scheme documents and approval records must be retained for 3 years for audit purposes.

## Data
- Source: dealer_master, dealer_sales_data, brand_agreement_master, campaign_briefs, active_manufacturer_promos
- Output: promo_schemes table, dealer_participation_log, promo_kpi_snapshots
