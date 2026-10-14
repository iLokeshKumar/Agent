# Skill: P2 Sourcing / RFQ

## Identity
Runs RFQs for non-catalogue and project-BOM items — the AV fit-out and
systems-integration lines — issuing to approved vendors and building a
normalised comparison for award. Suite: Purchase. Runtime: headless
collection; buyer review on award.

## Modes
| Mode | Does | Status set |
|---|---|---|
| `issue` | Creates the RFQ for the named approved vendors | `ISSUED` |
| `collect` | Files vendor quotes against an issued RFQ (revisions append, never overwrite) | `QUOTES_PENDING` |
| `compare` | Normalises, ranks, recommends — and records the award after buyer approval | `AWARDED` |

Mode may be set explicitly or is inferred: `quotes`+`rfq_number` →
collect, `rfq_number` alone → compare, otherwise issue.

## Procedure
1. **Validate** structurally: items with name and quantity > 0, an
   explicit approved-vendor list on issue, quote prices present on
   collect.
2. **Issue** — RFQ number from the Postgres sequence (`RFQ-YYYYMM-#####`).
   The row itself is persisted by the executor's recorder, so the
   document and the task status land in one transaction and a retried
   task cannot issue a second RFQ to the vendors.
3. **Collect** — each quote is one row per (rfq, vendor, revision); a
   vendor's revision never overwrites the earlier offer. Quotes filed
   by a retried task are not appended twice (keyed on the task).
4. **Compare** — normalise every quote, rank the comparable ones
   cheapest-first, and flag unparseable quotes to the buyer without
   ranking them. Mixed currencies refuse to rank (no FX in the
   platform) — the comparison escalates instead.
5. **Award gate** — recommend in `AWARD_RECOMMENDATION` and stop. After
   buyer approval the re-run records the award and hands
   `award_payload` (vendor, items, currency, rfq_number) to P1.

## Tool bindings
| Tool | Use |
|---|---|
| numbering service | RFQ sequence |
| RFQ repository | RFQ + quote documents |
| mail lane (outlook) | RFQ dispatch / quote collection — pending mail-lane go-live; until then quotes arrive through the API |

## Autonomy & human-in-the-loop
- A1 by default. Award is ALWAYS the buyer's decision (`AWARD_RECOMMENDATION`);
  re-runs after grant must record the award without asking twice
  (`_approval_granted`).
- A2 is permitted for reminders only (chasing outstanding quotes).

## Failure behaviour
- Invalid payload → VALIDATION_ERROR, nothing written.
- Unknown RFQ on collect/compare → VALIDATION_ERROR (never invent a
  document that was not issued).
- No comparable quotes at all → VALIDATION_ERROR, not a fake ranking.

## KPIs
Quote turnaround < 2 hrs; approved-vendor coverage per category.

## Never
- Award a vendor autonomously — recommend, never select.
- Rank a quote that failed to normalise or lacks a price.
- Compare across currencies without an FX rate.
- Overwrite a vendor's earlier quote — revisions append.
