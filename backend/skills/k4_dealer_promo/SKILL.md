# Dealer Promo — K4
**Version:** 1.0.0  
**Autonomy:** A1  
**Go-live:** 2026-09-11  
**Primary KPI:** `promo_conversion_rate`  
**KPI Target:** ≥ 10%

## Purpose
Designs and manages dealer-facing promotional schemes (trade discounts, volume incentives, spiff programmes) to drive sell-out performance. Prepares promo scheme documents for Marketing Manager approval and tracks dealer participation and conversion. All schemes are advisory (A1) — no promotional commitment is made until approved.

## Trigger
Triggered on demand by the Marketing Manager when designing a new dealer promotion, or automatically when sales velocity for a product category falls below the monthly target.

## Actions
| Action | Description | Autonomy |
|--------|-------------|----------|
| analyse_dealer_performance | Review current dealer sell-out data vs targets | A1 |
| draft_promo_scheme | Design scheme structure: incentive type, eligibility, value, duration | A1 |
| map_eligible_dealers | Filter dealer master for scheme-eligible participants | A1 |
| generate_scheme_doc | Produce formatted scheme document for approval | A1 |
| submit_for_approval | Route scheme to Marketing Manager approval queue | A1 (human gate) |
| track_participation | Record dealer opt-ins and scheme-linked sales | A1 |

## Constraints
- Never commit a promotional scheme to dealers without Marketing Manager approval.
- Never create a scheme that conflicts with an active manufacturer promotion.
- Promo values must not exceed the pre-approved trade margin as per brand agreements.
- Scheme eligibility criteria must be objective and consistently applied across all dealers.
- Conversion rate below 10% must trigger a scheme effectiveness review within 7 days.
