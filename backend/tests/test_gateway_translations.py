"""Webhook payload translators — contract tests.

A translator's job: provider JSON in, clean task payload out — and a
loud ValueError when there is nothing actionable, never a silently
empty description.
"""

import pytest
from gateway.translations import translate


class TestWaba:

    def test_bsp_envelope_service_intent(self):
        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "contacts": [{"wa_id": "919999999999"}],
                        "messages": [{
                            "from": "919999999999",
                            "text": {"body":
                                     "My display is not working"},
                        }],
                    },
                }],
            }],
        }
        task_type, task_payload = translate("waba", payload)
        assert task_type == "service_request"
        assert task_payload["description"] == (
            "My display is not working"
        )
        assert task_payload["channel"] == "whatsapp"
        assert task_payload["sender"] == "919999999999"

    def test_flat_payload_order_intent(self):
        task_type, task_payload = translate("waba", {
            "text": "please send quote for 5 units",
        })
        assert task_type == "purchase_request"
        assert task_payload["channel"] == "whatsapp"

    def test_empty_text_rejected(self):
        with pytest.raises(ValueError):
            translate("waba", {"entry": []})

    def test_no_text_at_all_rejected(self):
        with pytest.raises(ValueError):
            translate("waba", {"foo": "bar"})


class TestGraph:

    def test_email_subject_becomes_request(self):
        task_type, task_payload = translate("graph", {
            "subject": "Need 10 laptops for site office",
            "body_preview": "urgent",
        })
        assert task_type == "purchase_request"
        assert task_payload["description"] == (
            "Need 10 laptops for site office"
        )
        assert task_payload["channel"] == "email"

    def test_empty_email_rejected(self):
        with pytest.raises(ValueError):
            translate("graph", {})


class TestZohoBooks:

    def test_books_webhook_is_by_reference(self):
        task_type, task_payload = translate("zoho_books", {
            "invoice_id": "inv-9",
        })
        assert task_type == "books_to_tally"
        # Webhook bodies are untrusted; the sync re-reads from the API.
        assert task_payload["trigger"] == "webhook"


class TestUnknownSource:

    def test_unknown_source_rejected(self):
        with pytest.raises(ValueError):
            translate("carrier_pigeon", {})
