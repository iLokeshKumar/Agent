"""Phase-1 go-live audit — which credentials are still missing.

Read-only. Prints configured/missing booleans only — never secret
values — so the output is safe to paste into tickets and chats.

Usage:
    cd backend && python scripts/audit_go_live.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402


def _flag(label: str, ok: bool, required: str) -> dict:
    return {
        "area": label,
        "ok": ok,
        "required_for": required,
    }


def audit() -> dict:
    zoho = all([
        settings.zoho_client_id,
        settings.zoho_client_secret,
        settings.zoho_refresh_token,
        settings.zoho_org_id,
    ])

    tally = bool(settings.tally_host)

    waba = all([
        settings.waba_provider,
        settings.waba_api_key,
        settings.waba_api_url,
    ])

    cloud = bool(settings.cloud_database_url)

    checks = [
        _flag("Zoho Books OAuth (F3)", zoho,
              "Phase-1 F3 go-live"),
        _flag("Tally host reachable config (F3 posting)", tally,
              "Phase-1 F3 go-live"),
        _flag("WhatsApp BSP (S1/C-lanes)", waba,
              "Phase-1 S1 + serialised GRN flow"),
        _flag("Mail POP3 intake", bool(settings.pop3_host and settings.pop3_username),
              "Mail lane"),
        _flag("Mail SMTP send (approval-gated)",
              bool(settings.smtp_host and settings.smtp_username),
              "Outbound sends — must stay off in dry-run"),
        _flag("Apollo enrichment (H1/K-suite)", bool(settings.apollo_api_key),
              "Phase-2/3 campaign & HR"),
        _flag("RocketReach enrichment (H1)", bool(settings.rocketreach_api_key),
              "Phase-2/3 HR sourcing"),
        _flag("Cloud analytics store (shipper/cloud_db)", cloud,
              "Nightly analytics chain + D1 marts"),
    ]

    return {
        "app_env": settings.app_env,
        "mail_send_enabled": settings.mail_send_enabled,
        "checks": checks,
        "all_critical_ok": all(c["ok"] for c in checks[:2]),
    }


def main() -> None:
    report = audit()
    print(f"APP_ENV = {report['app_env']}")
    print(f"MAIL_SEND_ENABLED = {report['mail_send_enabled']} "
          "(must be false until supervised-live is approved)")
    print()
    print(f"{'AREA':<44} {'SET?':<6} BLOCKS")
    print("-" * 78)
    for c in report["checks"]:
        print(f"{c['area']:<44} {'yes' if c['ok'] else 'NO':<6} {c['required_for']}")
    print("-" * 78)
    print(f"Phase-1 critical (Zoho + Tally): "
          f"{'READY' if report['all_critical_ok'] else 'BLOCKED'}")
    print()
    print("Missing pieces — where to set them (backend/.env):")
    print("  Zoho:      ZOHO_CLIENT_ID/SECRET/REFRESH_TOKEN/ORG_ID (+ACCESS_TOKEN)")
    print("  Tally:     TALLY_HOST (port defaults 9000; see docs/tally-network-setup.md)")
    print("  WhatsApp:  WABA_PROVIDER/WABA_API_KEY/WABA_API_URL (+channel id)")
    print("  Mail:      POP3_HOST/USERNAME/PASSWORD, SMTP_* (keep MAIL_SEND_ENABLED=false)")
    print("  Cloud:     CLOUD_DATABASE_URL=postgresql://…/yexis_agent_cloud")
    print("  Apollo:    APOLLO_API_KEY    RocketReach: ROCKETREACH_API_KEY")


if __name__ == "__main__":
    main()
