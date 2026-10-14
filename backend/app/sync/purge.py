"""DPDP purge job — retention-class deletion for PII-bearing rows.

The DPDP rule the spec imposes: personal data in conversational/candidate
classes must not outlive its retention period. Classes (set by the
Document AI ingestor on doc_index.retention_class):

  transactional  — keep 7 years (statutory; purge_after far future)
  conversational — keep 90 days (WA/mail bodies, contact extracts)
  candidate      — keep 30 days (HR candidate_pool / CV extracts)

Rows whose purge_after has passed are deleted outright; rows with a
class but no purge_after get it defaulted at ingest time by policy.

Verification (spec acceptance): the purge job must be verifiable on
synthetic aged rows — done in tests/test_cloud_db.py with seeded
expired conversational rows.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.enterprise import DocIndex, EmailMessage

logger = logging.getLogger(__name__)

# Retention classes → max age in days. `transactional` is governed by
# its explicit purge_after (statutory 7y), never swept by age alone.
RETENTION_DAYS = {
    "conversational": 90,
    "candidate": 30,
}


def purge_expired(local_db: Session, now: datetime | None = None) -> dict:
    """Delete expired PII rows. Returns counts per table for the audit log."""
    now = now or datetime.now(timezone.utc)

    deleted: dict[str, int] = {}

    # ── doc_index: explicit purge_after always wins.
    q = (
        local_db.query(DocIndex)
        .filter(DocIndex.purge_after.isnot(None))
        .filter(DocIndex.purge_after < now)
    )
    expired_docs = q.all()
    for d in expired_docs:
        local_db.delete(d)
    deleted["doc_index"] = len(expired_docs)

    # ── email bodies in conversational class: age-based.
    cutoff_conv = now - timedelta(
        days=RETENTION_DAYS["conversational"]
    )
    q = (
        local_db.query(EmailMessage)
        .filter(EmailMessage.status.in_(["received", "draft", "failed"]))
        .filter(EmailMessage.created_at < cutoff_conv)
    )
    expired_mail = q.all()
    for m in expired_mail:
        local_db.delete(m)
    deleted["email_message"] = len(expired_mail)

    local_db.commit()

    total = sum(deleted.values())
    if total:
        logger.warning(
            "DPDP purge removed %d expired rows: %s",
            total,
            deleted,
        )
    return {
        "status": "completed",
        "deleted": deleted,
        "total": total,
        "ran_at": now.isoformat(),
    }
