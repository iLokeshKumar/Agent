from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.ui import htmx_mail_inbox
from app.models.agent import Base
from app.models.enterprise import EmailMessage


def _inbox_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[EmailMessage.__table__])
    return sessionmaker(bind=engine)()


def test_mail_inbox_paginates_newest_first_and_keeps_search():
    db = _inbox_db()
    try:
        received = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.add_all([
            EmailMessage(
                direction="in",
                status="received",
                sender=f"sender-{index}@example.com",
                subject=f"Message {index:02}",
                body="urgent follow-up" if index == 4 else "regular message",
                created_at=received + timedelta(minutes=index),
            )
            for index in range(1, 13)
        ])
        db.commit()

        first = htmx_mail_inbox(db=db).body.decode()
        assert first.index("Message 12") < first.index("Message 11")
        assert "Message 03" in first
        assert "Message 02" not in first
        assert "Page 1 of 2" in first
        assert "/htmx/mail/inbox?page=2" in first

        second = htmx_mail_inbox(page=2, q="urgent", db=db).body.decode()
        assert "Message 04" in second
        assert "1 message" in second

        filtered = htmx_mail_inbox(q="urgent", db=db).body.decode()
        assert "Message 04" in filtered
        assert "Message 12" not in filtered
    finally:
        db.close()
