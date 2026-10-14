from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from app.repository.task_repository import TaskRepository


def test_stale_task_detection_uses_each_task_timeout():
    now = datetime.now(timezone.utc)
    timed_out = SimpleNamespace(
        started_at=now - timedelta(seconds=301),
        timeout_seconds=300,
    )
    still_running = SimpleNamespace(
        started_at=now - timedelta(seconds=299),
        timeout_seconds=300,
    )
    long_timeout = SimpleNamespace(
        started_at=now - timedelta(seconds=301),
        timeout_seconds=600,
    )
    db = Mock()
    db.execute.return_value.scalars.return_value.all.return_value = [
        timed_out,
        still_running,
        long_timeout,
    ]

    assert TaskRepository(db).recover_stale_tasks() == [timed_out]
