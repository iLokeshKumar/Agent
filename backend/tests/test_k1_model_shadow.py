from unittest.mock import MagicMock

from app.agents.k1_campaign_brief import K1CampaignBrief
from app.services.model_provider import ModelProviderError, ModelResponse


class _FakeModelClient:
    def __init__(self, response=None, error=None):
        self.response = response or ModelResponse(
            content="Draft only; confirm the offer before use.",
            provider="test-provider",
            model="test-model",
            input_tokens=20,
            output_tokens=9,
        )
        self.error = error
        self.call = None

    def complete(self, **kwargs):
        self.call = kwargs
        if self.error:
            raise self.error
        return self.response


def _db():
    db = MagicMock()
    return db


def _payload():
    return {
        "action": "create",
        "name": "Internal Q4 dealer campaign",
        "campaign_type": "dealer_promo",
        "default_channel": "whatsapp",
        "objective": "Announce an approved product range",
        "target_segment": "Existing regional dealers",
        "entity": "YEXIS-EL",
        "budget": 50000,
        "target_skus": ["SKU-PRIVATE"],
        "campaign_ref": "CMP-PRIVATE",
    }


def test_k1_shadow_is_off_by_default_and_does_not_change_result(
    monkeypatch,
):
    from app.agents import k1_campaign_brief

    monkeypatch.setattr(
        k1_campaign_brief.settings, "k1_model_shadow_enabled", False
    )
    client = _FakeModelClient()
    db = _db()
    agent = K1CampaignBrief(model_client=client)

    result = agent._run(db, _payload())

    assert result.status == "SUCCESS"
    assert "model_shadow" not in result.output
    assert result.output["status"] == "draft"
    assert result.actions_taken == ["campaign_brief_created"]
    assert client.call is None
    db.commit.assert_called_once()


def test_k1_shadow_returns_advisory_draft_and_never_sends(
    monkeypatch,
):
    from app.agents import k1_campaign_brief

    monkeypatch.setattr(
        k1_campaign_brief.settings, "k1_model_shadow_enabled", True
    )
    client = _FakeModelClient()
    db = _db()
    result = K1CampaignBrief(model_client=client)._run(db, _payload())

    assert result.status == "SUCCESS"
    assert result.output["model_shadow"] == {
        "mode": "shadow",
        "status": "drafted",
        "provider": "test-provider",
        "model": "test-model",
        "draft": "Draft only; confirm the offer before use.",
        "input_tokens": 20,
        "output_tokens": 9,
    }
    assert result.output["status"] == "draft"
    assert result.actions_taken == ["campaign_brief_created"]
    assert db.add.call_count == 1
    prompt = client.call["messages"][0].content
    assert "Internal Q4 dealer campaign" not in prompt
    assert "SKU-PRIVATE" not in prompt
    assert "CMP-PRIVATE" not in prompt
    assert "50000" not in prompt


def test_k1_provider_failure_is_visible_without_blocking_brief(
    monkeypatch,
):
    from app.agents import k1_campaign_brief

    monkeypatch.setattr(
        k1_campaign_brief.settings, "k1_model_shadow_enabled", True
    )
    client = _FakeModelClient(
        error=ModelProviderError("test-provider returned HTTP 503")
    )
    db = _db()
    result = K1CampaignBrief(model_client=client)._run(db, _payload())

    assert result.status == "SUCCESS"
    assert result.output["status"] == "draft"
    assert result.output["model_shadow"]["status"] == "error"
    assert "HTTP 503" in result.output["model_shadow"]["error"]
    db.commit.assert_called_once()
