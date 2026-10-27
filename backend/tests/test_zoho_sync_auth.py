from app.integrations.zoho_books import ZohoBooksError
from app.sync import syncer as syncer_module


def test_full_sync_stops_before_targets_when_auth_refresh_fails(monkeypatch):
    class FakeClient:
        configured = True

        def ensure_authenticated(self):
            raise ZohoBooksError(400, "Token refresh was rate limited")

    class FakeCache:
        def __init__(self):
            self.writes = []

        def put(self, key, value):
            self.writes.append((key, value))

    syncer = syncer_module.ZohoSyncer(cache=FakeCache())
    target_calls = []

    monkeypatch.setattr(syncer_module, "get_zoho_client", lambda: FakeClient())
    monkeypatch.setattr(
        syncer,
        "_sync_target",
        lambda target, _client: target_calls.append(target),
    )

    result = syncer.sync_all()

    assert result["status"] == "error"
    assert target_calls == []
    assert set(result["results"]) == set(syncer_module.SYNC_TARGETS)
    assert all(item["status"] == "error" for item in result["results"].values())
    assert syncer.cache.writes[0][0] == "_sync_status"
