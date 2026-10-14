"""Cloud-store verification — proves the provisioning is real.

Run after setting CLOUD_DATABASE_URL in backend/.env:

    cd backend && python scripts/verify_cloud_store.py

Steps (all idempotent, safe to re-run):
  1. Read CLOUD_DATABASE_URL from settings (never prints secrets).
  2. Connect and SELECT version()/now().
  3. Create the mirror tables (kpi_snapshot, dealer_mart, sku_health,
     installed_base) — same idempotent DDL the nightly shipper runs.

Exit code 0 = store usable by the nightly chain and /mcp/cloud_db.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.db import (  # noqa: E402
    CloudDatabaseNotConfigured,
    cloud_database_url,
    get_cloud_engine,
)


def main() -> int:
    print("[1/3] Config")
    try:
        url = cloud_database_url()
    except CloudDatabaseNotConfigured as exc:
        print(f"  [FAIL] {exc}")
        print("  Fix: set CLOUD_DATABASE_URL=postgresql://…/yexis_agent_cloud"
              " in backend/.env")
        return 1
    if url is None:
        print("  [FAIL] CLOUD_DATABASE_URL is empty — cloud store unconfigured")
        print("  Fix: set CLOUD_DATABASE_URL=postgresql://…/yexis_agent_cloud"
              " in backend/.env")
        return 1
    # Show host/db only — never credentials.
    safe = url.split("@")[-1] if "@" in url else url
    print(f"  [OK] configured (host/db: {safe})")

    print("[2/3] Connectivity")
    try:
        engine = get_cloud_engine()
        with engine.connect() as conn:
            row = conn.execute(text("select version(), now()")).fetchone()
        print(f"  [OK] {str(row[0])[:60]}")
        print(f"  [OK] server time: {row[1]}")
    except Exception as exc:
        print(f"  [FAIL] connect failed: {exc}")
        print("  Fix: check host/port/user/password and that the server"
              " allows your IP (firewall/IP allow-list)")
        return 1

    print("[3/3] Mirror schema")
    try:
        from app.models.agent import Base
        from app.models import enterprise as ent

        tables = [getattr(ent, n).__table__ for n in (
            "KpiSnapshot", "DealerMart", "SkuHealth", "InstalledBase",
        )]
        Base.metadata.create_all(bind=engine, tables=tables)
        with engine.connect() as conn:
            names = sorted(
                r[0] for r in conn.execute(
                    text("select tablename from pg_tables"
                         " where schemaname='public'")
                ).fetchall()
            )
        need = [t.name for t in tables]
        missing = [n for n in need if n not in names]
        if missing:
            print(f"  [FAIL] missing tables after create_all: {missing}")
            return 1
        print(f"  [OK] mirror tables present: {need}")
    except Exception as exc:
        print(f"  [FAIL] schema step: {exc}")
        return 1

    print()
    print("READY — nightly shipper and /mcp/cloud_db can use this store.")
    print("Next: run the nightly chain (or POST /sync/ship) to push"
          " the first mirrors; then GET /mcp/cloud_db/kpi should be 200.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
