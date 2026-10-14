# Policy: P2 Sourcing / RFQ

## Award authority (human-in-the-loop)
| Decision | Route |
|---|---|
| Vendor selection (award) | Buyer — ALWAYS; P2 only recommends |
| Quote reminders / chasers | A2 autonomous permitted |
| Award above buyer authority | Escalates with P1's approval matrix on PO issuance (Branch Head 50k–5L · GM 5L–25L · MD >25L) |

## Ranking rules
- Only quotes with `parse_status == "normalised"` are ranked; a quote
  with no price is `price_missing` and is shown to the buyer excluded
  from the ranking, never silently dropped.
- Ranking is cheapest comparable quote first; `lead_time_days` is
  displayed for the buyer's judgement but does not reorder the ranking.
- A single comparable quote is still recommendable, with an explicit
  `warning: awarding without competition`.
- Mixed-currency comparisons refuse to rank — no FX conversion exists
  in the platform; the agent returns VALIDATION_ERROR rather than an
  approximation.

## Quote integrity
- Quotes are append-only: a revision is a new row; the original offer
  is never overwritten.
- One vendor's quotes are keyed by (rfq, vendor); a retried task does
  not file the same quote twice (source_task_id dedupe).
- Quotes are only filed against an RFQ that exists — no quoting against
  a document that was never issued.

## Idempotency
- RFQ issuance is keyed on the task: a re-queued or retried task must
  not send vendors a second RFQ (UNIQUE on `correlation_id`, recorder
  check on `task_id`).
- Award recording is a field update — safe to re-run.

## Escalation
- Unknown RFQ on collect/compare → VALIDATION_ERROR to the requester.
- All quotes unparseable → VALIDATION_ERROR, buyer informed; the agent
  never fabricates a ranking from partial data.
- Email dispatch of RFQs awaits the mail-lane go-live; quotes arrive
  via API until then.
