from types import SimpleNamespace
from unittest.mock import Mock
import uuid

from app.api import demo


def test_demo_task_uses_signed_in_requester(monkeypatch):
    created = {}
    task = SimpleNamespace(id="task-id", correlation_id="correlation-id")

    class FakeTaskRepository:
        def __init__(self, db):
            self.db = db

        def create(self, **kwargs):
            created.update(kwargs)
            return task

    queue = Mock()
    monkeypatch.setattr(demo, "TaskRepository", FakeTaskRepository)
    monkeypatch.setattr(demo, "task_queue", queue)
    monkeypatch.setattr(
        "app.integrations.zoho_books.get_zoho_client",
        lambda: SimpleNamespace(configured=True),
    )
    db = Mock()

    result = demo._run_scenario("f3_books_tally", db, requested_by="lokesh")

    assert created["requested_by"] == "lokesh"
    assert result["task_id"] == "task-id"
    queue.put.assert_called_once_with(task_id="task-id", priority=5)


def test_p1_placeholder_purchase_request_is_blocked(monkeypatch):
    create = Mock()

    class FakeTaskRepository:
        def __init__(self, db):
            pass

        def create(self, **kwargs):
            create(**kwargs)

    monkeypatch.setattr(demo, "TaskRepository", FakeTaskRepository)

    try:
        demo._run_scenario("p1_purchase", Mock(), requested_by="lokesh")
    except demo.HTTPException as exc:
        assert exc.status_code == 409
        assert "fixed example data" in exc.detail
    else:
        raise AssertionError("placeholder purchase request must not be queued")

    create.assert_not_called()


def test_demo_api_uses_authenticated_requester(monkeypatch):
    run = Mock(return_value={"task_id": "task-id"})
    actor = SimpleNamespace(username="lokesh", can_run_demo=lambda _scenario: True)
    monkeypatch.setattr("app.core.auth.get_current_user", lambda _request: actor)
    monkeypatch.setattr(demo, "_run_scenario", run)

    response = demo.run_scenario("p1_purchase", request=Mock(), db=Mock())

    assert response == {"task_id": "task-id"}
    run.assert_called_once()
    assert run.call_args.args[0] == "p1_purchase"
    assert run.call_args.kwargs["requested_by"] == "lokesh"


def test_f1_empty_zoho_response_stays_empty(monkeypatch):
    client = SimpleNamespace(
        configured=True,
        list_invoices=Mock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.integrations.zoho_books.get_zoho_client",
        lambda: client,
    )

    payload, snapshot = demo._zoho_invoices_for_f1()

    assert payload["invoices"] == []
    assert snapshot["data_mode"] == "live"
    assert snapshot["invoice_count"] == 0
    assert "No overdue invoices" in snapshot["note"]
    client.list_invoices.assert_called_once_with(status="overdue", per_page=25)


def test_f3_demo_does_not_inject_sample_journals(monkeypatch):
    task = SimpleNamespace(id=uuid.uuid4(), correlation_id=uuid.uuid4())
    created = {}

    class FakeTaskRepository:
        def __init__(self, db):
            pass

        def create(self, **kwargs):
            created.update(kwargs)
            return task

    client = SimpleNamespace(configured=True)
    client.list_journals = Mock(side_effect=AssertionError("must fetch in the worker"))
    monkeypatch.setattr(
        "app.integrations.zoho_books.get_zoho_client",
        lambda: client,
    )
    monkeypatch.setattr(demo, "TaskRepository", FakeTaskRepository)
    monkeypatch.setattr(demo, "task_queue", Mock())

    result = demo._run_scenario("f3_books_tally", Mock(), requested_by="operator")

    assert "journal_entries" not in created["payload"]
    assert result["zoho_snapshot"]["data_mode"] == "live"
    client.list_journals.assert_not_called()


def test_m2_demo_reports_existing_mart_counts_without_seeding(monkeypatch):
    task_ids = iter([uuid.uuid4(), uuid.uuid4()])

    class FakeTaskRepository:
        def __init__(self, db):
            pass

        def create(self, **kwargs):
            return SimpleNamespace(id=next(task_ids), correlation_id=uuid.uuid4())

    db = Mock()
    db.query.return_value.filter.return_value.count.side_effect = [4, 7]
    monkeypatch.setattr(demo, "TaskRepository", FakeTaskRepository)
    monkeypatch.setattr(demo, "task_queue", Mock())

    result = demo._run_scenario("m2_gsm_brief", db, requested_by="operator")

    assert result["zoho_snapshot"]["dealers"] == 4
    assert result["zoho_snapshot"]["kpi_rows"] == 7
    assert result["zoho_snapshot"]["data_mode"] == "live"
    assert result["zoho_snapshot"]["note"] == "Reads current records from the local analytics mart."
