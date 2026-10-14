# Tests

```bash
cd backend
pytest tests/ -q                # unit + contract (SQLite, no services)
pytest tests/ -v                # verbose
pytest tests/redteam/ -q        # mandatory red-team pack (CI checks this)
pytest tests/test_tally_gateway.py -q
pytest tests/test_gateway_translations.py -q
```

- `conftest.py` — fixtures (DB, client, seeds)
- `test_all_agents.py` — P1/S1/P4/F3/F1/D1/V1 validate+execute
- `test_lane_governance.py`, `test_gateway_translations.py`, `test_tally_gateway.py` etc.
- `redteam/test_redteam_pack.py` — policy bypass attempts (must pass in CI)

Vertical checks (require live `http://127.0.0.1:8000` + Postgres):

```bash
python ../lifecycle_check.py   # P1 four scenarios
python ../p2p_check.py         # PR→PO→GRN→Invoice→Tally (reads SQL)
```
