from types import SimpleNamespace
from unittest.mock import Mock
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from starlette.requests import Request

from app.api import auth as auth_api
from app.api import task as task_api
from app.integrations import zoho_books
from app.integrations import zoho_crm
from app.services import zoho_connections


def test_refresh_token_encryption_round_trips_and_is_not_plaintext(monkeypatch):
    monkeypatch.setattr(zoho_connections.settings, "zoho_token_encryption_key", "stable-test-key")

    encrypted = zoho_connections.encrypt_refresh_token("refresh-token-value")

    assert encrypted != "refresh-token-value"
    assert zoho_connections.decrypt_refresh_token(encrypted) == "refresh-token-value"


def test_user_without_personal_zoho_uses_shared_client(monkeypatch):
    shared_client = object()
    monkeypatch.setattr(zoho_books, "_client", shared_client)
    monkeypatch.setattr(zoho_connections, "get_user_zoho_connection", lambda _username: None)

    with zoho_books.zoho_user_context("operator"):
        client = zoho_books.get_zoho_client()

    assert client is shared_client


def test_personal_zoho_client_persists_refresh_without_touching_shared_client(monkeypatch):
    updates = []
    monkeypatch.setattr(zoho_books, "_client", object())
    monkeypatch.setattr(
        zoho_connections,
        "get_user_zoho_connection",
        lambda _username: (
            {
                "refresh_token": "personal-refresh",
                "organization_id": "org-123",
                "data_center": "in",
            },
            "user-id",
        ),
    )
    monkeypatch.setattr(
        zoho_connections,
        "update_user_zoho_refresh_token",
        lambda username, refresh: updates.append((username, refresh)),
    )

    with zoho_books.zoho_user_context("operator"):
        client = zoho_books.get_zoho_client()

    assert client.organization_id == "org-123"
    assert client.refresh_token == "personal-refresh"
    client._token_save_callback("access", "rotated-refresh", "org-123")
    assert updates == [("operator", "rotated-refresh")]


def test_crm_client_uses_active_zoho_connection(monkeypatch):
    selected = SimpleNamespace(
        client_id="oauth-client",
        client_secret="oauth-secret",
        refresh_token="personal-refresh",
        data_center="eu",
        access_token="access-token",
        _token_expiry=12345,
        _token_save_callback=None,
    )
    monkeypatch.setattr(zoho_books, "get_zoho_client", lambda: selected)

    client = zoho_crm.get_zoho_crm_client()

    assert client.refresh_token == "personal-refresh"
    assert client.base_url == zoho_crm.ZOHO_CRM_BASES["eu"]
    assert client._access_token == "access-token"


def test_task_api_binds_requester_to_signed_in_user(monkeypatch):
    task = SimpleNamespace(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        priority=5,
        task_type="collections",
        requested_by="operator",
    )
    created = {}

    class FakeTaskRepository:
        def __init__(self, _db):
            pass

        def create(self, **kwargs):
            created.update(kwargs)
            return task

    monkeypatch.setattr(task_api, "TaskRepository", FakeTaskRepository)
    monkeypatch.setattr(task_api.task_queue, "put", Mock())
    monkeypatch.setattr(
        task_api,
        "get_current_user",
        lambda _request: SimpleNamespace(
            username="operator",
            can=lambda section, action="view": (
                section == "tasks" and action == "edit"
            ),
        ),
    )
    http_request = Mock()
    http_request.headers = {}

    response = task_api.create_task(
        task_api.CreateTaskRequest(task_type="collections"),
        http_request=http_request,
        db=Mock(),
    )

    assert created["requested_by"] == "operator"
    assert response["requested_by"] == "operator"


def test_task_api_rejects_requester_impersonation(monkeypatch):
    monkeypatch.setattr(
        task_api,
        "get_current_user",
        lambda _request: SimpleNamespace(
            username="operator",
            can=lambda _section, _action="view": True,
        ),
    )
    http_request = Mock()
    http_request.headers = {}

    try:
        task_api.create_task(
            task_api.CreateTaskRequest(task_type="collections", requested_by="admin"),
            http_request=http_request,
            db=Mock(),
        )
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "signed-in user" in exc.detail
    else:
        raise AssertionError("a task must not impersonate another requester")


def test_zoho_oauth_callback_rejects_signed_state_without_nonce(monkeypatch):
    state = auth_api.jwt.encode(
        {
            "sub": "operator",
            "uid": str(uuid.uuid4()),
            "purpose": "zoho_user_connect",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        auth_api._secret(),
        algorithm=auth_api.ALGORITHM,
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/auth/zoho/callback",
            "headers": [],
            "query_string": b"",
            "server": ("localhost", 443),
            "client": ("127.0.0.1", 12345),
        }
    )
    get_client = Mock(side_effect=AssertionError("token exchange must not run"))
    monkeypatch.setattr(auth_api, "get_zoho_client", get_client)

    response = auth_api.zoho_callback(request=request, code="untrusted-code", state=state)

    assert response.status_code == 400
    get_client.assert_not_called()


def test_zoho_oauth_callback_accepts_one_time_state_without_same_host_cookie(monkeypatch):
    user_id = str(uuid.uuid4())
    nonce = "one-time-test-nonce"
    state = auth_api.jwt.encode(
        {
            "sub": "operator",
            "uid": user_id,
            "purpose": "zoho_user_connect",
            "nonce": nonce,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        auth_api._secret(),
        algorithm=auth_api.ALGORITHM,
    )
    consumed = []
    saved = []
    monkeypatch.setattr(
        zoho_connections,
        "consume_user_oauth_state",
        lambda uid, username, actual_nonce: (
            consumed.append((uid, username, actual_nonce)) or True
        ),
    )
    monkeypatch.setattr(
        zoho_connections,
        "save_user_zoho_connection",
        lambda **kwargs: saved.append(kwargs),
    )

    class FakeZohoClient:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.data_center = self.__dict__.get("data_center", "in")
            self.client_id = "client-id"
            self.client_secret = "client-secret"
            self.refresh_token = self.__dict__.get("refresh_token")

        def get_organization(self):
            return {"organization_id": "org-1", "name": "Acme"}

    monkeypatch.setattr(auth_api, "get_zoho_client", FakeZohoClient)
    token_response = Mock()
    token_response.json.return_value = {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "expires_in": 3600,
    }
    monkeypatch.setattr("httpx.post", Mock(return_value=token_response))
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/auth/zoho/callback",
            "headers": [],
            "query_string": b"",
            "server": ("localhost", 8000),
            "client": ("127.0.0.1", 12345),
        }
    )

    response = auth_api.zoho_callback(request=request, code="authorization-code", state=state)

    assert response.status_code == 200
    assert consumed == [(user_id, "operator", nonce)]
    assert saved[0]["user_id"] == user_id
    assert saved[0]["organization_id"] == "org-1"


def test_gateway_token_submits_as_configured_enabled_user(monkeypatch):
    task = SimpleNamespace(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        priority=100,
        requested_by="service-admin",
    )
    created = {}

    class FakeTaskRepository:
        def __init__(self, _db):
            pass

        def create(self, **kwargs):
            created.update(kwargs)
            return task

    service_user = SimpleNamespace(
        id=uuid.uuid4(),
        username="service-admin",
        role="admin",
    )
    db = Mock()
    db.query.return_value.filter.return_value.one_or_none.return_value = service_user
    monkeypatch.setattr(task_api, "TaskRepository", FakeTaskRepository)
    monkeypatch.setattr(task_api.task_queue, "put", Mock())
    monkeypatch.setattr(task_api, "get_current_user", lambda _request: None)
    monkeypatch.setattr(task_api.settings, "gateway_api_token", "gateway-test-secret")
    monkeypatch.setattr(task_api.settings, "gateway_service_username", "service-admin")
    request = Mock()
    request.headers = {"Authorization": "Bearer gateway-test-secret"}

    response = task_api.create_task(
        task_api.CreateTaskRequest(task_type="campaign_scheduler"),
        http_request=request,
        db=db,
    )

    assert response["requested_by"] == "service-admin"
    assert created["requested_by"] == "service-admin"


def test_gateway_token_is_not_required_for_a_valid_user_session(monkeypatch):
    from gateway.internal_auth import task_submission_headers

    monkeypatch.setattr(task_api.settings, "gateway_api_token", "")

    assert task_submission_headers() == {}
