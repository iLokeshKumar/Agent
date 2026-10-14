# Policy: D1 Ask-the-Business

## Read scope
- Branch-scoped row security; an MD-level query sees all branches,
  a branch user sees their own.
- Figures always carry: source system, period, generated-at.

## Digest contract
- 08:00 IST delivery, >= 95% of days.
- Every anomaly includes a pre-analysed probable cause.

## Prohibited
- Any write to Books/CRM/Tally (enforced: read-only tool set).
- Presenting placeholder/fallback data without the
  `source: payload_fallback` marker.
