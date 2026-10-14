"""Mailbox service tests.

Exercises the approval-only draft workflow and the POP3 duplicate guard
without talking to a real mailbox.
"""

import uuid
import socket
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.models.agent import Base
from app.models.approval import Approval
from app.models.enterprise import EmailMessage
from app.services.mailbox import (
    configuration_status,
    poll_pop3,
    send_approved_draft,
)
from app.api.mail import poll as poll_mail


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[EmailMessage.__table__, Approval.__table__])
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_configuration_status_reflects_settings(monkeypatch):
    monkeypatch.setattr(settings, "pop3_host", "mail.example.com")
    monkeypatch.setattr(settings, "pop3_username", "user@example.com")
    monkeypatch.setattr(settings, "pop3_password", "secret")
    monkeypatch.setattr(settings, "smtp_host", "mail.example.com")
    monkeypatch.setattr(settings, "smtp_username", "user@example.com")
    monkeypatch.setattr(settings, "smtp_password", "secret")
    monkeypatch.setattr(settings, "mail_send_enabled", False)

    assert configuration_status() == {
        "pop3_configured": True,
        "smtp_configured": True,
        "smtp_send_enabled": False,
    }


def test_poll_pop3_deduplicates_messages(monkeypatch, db):
    class FakePOP3:
        def __init__(self, host, port, timeout=30):
            self.host = host
            self.port = port
            self.timeout = timeout

        def user(self, username):
            self.username = username

        def pass_(self, password):
            self.password = password

        def uidl(self):
            return "OK", [b"1 uid-1", b"2 uid-2"]

        def retr(self, number):
            if number == 1:
                return "OK", [b"From: sender@example.com", b"To: ops@example.com", b"Subject: First", b"", b"Body one"]
            return "OK", [b"From: sender@example.com", b"To: ops@example.com", b"Subject: Second", b"", b"Body two"]

        def quit(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(settings, "pop3_host", "mail.example.com")
    monkeypatch.setattr(settings, "pop3_username", "user@example.com")
    monkeypatch.setattr(settings, "pop3_password", "secret")
    monkeypatch.setattr("app.services.mailbox.poplib.POP3_SSL", FakePOP3)

    first = poll_pop3(db, limit=10)
    second = poll_pop3(db, limit=10)

    assert len(first) == 2
    assert len(second) == 0
    assert db.query(EmailMessage).filter_by(direction="in").count() == 2


def test_poll_endpoint_returns_gateway_error_for_dns_failure(monkeypatch):
    def fail_to_resolve(*args, **kwargs):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr("app.api.mail.poll_pop3", fail_to_resolve)

    with pytest.raises(HTTPException) as error:
        poll_mail(db=object())

    assert error.value.status_code == 502
    assert "DNS resolution" in error.value.detail


def test_send_approved_draft_requires_flag(monkeypatch, db):
    monkeypatch.setattr(settings, "mail_send_enabled", False)
    draft = EmailMessage(
        direction="out",
        status="pending_approval",
        recipient="ops@example.com",
        subject="Draft",
        body="Hello",
        correlation_id=uuid.uuid4(),
    )
    db.add(draft)
    db.commit()

    with pytest.raises(RuntimeError, match="disabled"):
        send_approved_draft(db, draft)


def test_send_approved_draft_marks_sent(monkeypatch, db):
    monkeypatch.setattr(settings, "mail_send_enabled", True)
    monkeypatch.setattr(settings, "smtp_host", "mail.example.com")
    monkeypatch.setattr(settings, "smtp_port", 465)
    monkeypatch.setattr(settings, "smtp_username", "user@example.com")
    monkeypatch.setattr(settings, "smtp_password", "secret")
    monkeypatch.setattr(settings, "smtp_from", "ops@example.com")

    class FakeSMTP:
        def __init__(self, host, port, timeout=30):
            self.host = host
            self.port = port
            self.timeout = timeout
            self.logged_in = None
            self.sent_messages = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def login(self, username, password):
            self.logged_in = (username, password)

        def send_message(self, message):
            self.sent_messages.append(message)

    monkeypatch.setattr("app.services.mailbox.smtplib.SMTP_SSL", FakeSMTP)

    draft = EmailMessage(
        direction="out",
        status="pending_approval",
        recipient="ops@example.com",
        subject="Draft",
        body="Hello",
        correlation_id=uuid.uuid4(),
    )
    db.add(draft)
    db.commit()

    updated = send_approved_draft(db, draft)

    assert updated.status == "sent"
    assert updated.sent_at is not None
    assert updated.provider_message_id
