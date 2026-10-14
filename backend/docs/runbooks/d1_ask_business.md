# D1 Ask-the-Business — Agent Runbook

Suite: Dashboard | Autonomy: A1 | Go-live: 2026-09-09

## Purpose
Natural-language query interface over the nightly mart data. Serves the MD,
branch managers, and finance team. Does NOT write to any table.

## Normal operation
1. User submits a question via API or WhatsApp
2. D1 classifies the query (sales_summary, receivables_aging, etc.)
3. Routes to the appropriate mart read-tool in `skills/d1_ask_business/read_tools.py`
4. Returns structured JSON + formatted text

## Supported queries
See `skills/d1_ask_business/data_dictionary.md` for full list.

Key endpoints:
```
POST /agents/d1/run
Body: {"query": "What is our DSO this month?", "entity": "YEXIS-EL"}
```

## Data freshness
D1 reads from nightly marts (refreshed at 02:00). For real-time data,
queries fall back to the Zoho Books cache (last sync watermark).

To check when marts were last refreshed:
```sql
SELECT cursor FROM sync_watermark WHERE lane = 'NIGHTLY' AND source = 'analytics_chain';
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "no data for this period" | Nightly run didn't complete | Check `nightly_analytics` task log; run `/sync/nightly` manually |
| All KPIs returning null | kpi_snapshot table empty | Verify marts ran; check `kpi_snapshot` table |
| Zoho queries failing | Token expired | Re-run Zoho OAuth consent flow |
| Query classified wrong | New phrasing not matched | Add alias to `SUPPORTED_QUERIES` in `d1_ask_business.py` |

## Monitoring
- Check `kpi_snapshot` row count daily: should be 20 per entity per period
- Alert if `dealer_mart` refreshed_ts is > 26 hours old
