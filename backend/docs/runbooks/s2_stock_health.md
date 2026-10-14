# S2 Stock Health — Agent Runbook

Suite: Stores | Autonomy: A1

## Purpose
Reads `sku_health` mart (refreshed nightly) and generates stock health
reports. Proposes action recommendations (clear_slow, mark_eol, reorder)
but never executes them autonomously.

## Normal operation
1. Nightly: `build_sku_health()` refreshes `sku_health` table
2. S2 is queried on demand by M2 GSM Command or branch managers
3. Returns ageing analysis and action recommendations
4. A1 only: all recommendations require human approval before action

## Action recommendations
| Code | Meaning |
|---|---|
| `ok` | No action needed |
| `reorder` | Stock below threshold |
| `clear_slow` | 60+ day ageing, not EOL |
| `clear_eol` | EOL flag set |
| `write_off` | 90+ day ageing, EOL |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `weeks_cover` always null | No offtake feed connected | Normal — field reserved for future sales velocity feed |
| Ageing incorrect | `received_ts` null on old serials | Backfill `received_ts` from GRN records |
| Stock availability KPI low | Many reserved serials | Review reserved allocations in `serial_registry` |
