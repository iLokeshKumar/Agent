from app.agents import k2_audience_builder as audience
from app.core.config import settings
from app.integrations import zoho_mcp_client


def test_mcp_list_records_uses_requested_server_and_paginates(monkeypatch):
    calls = []

    class FakeClient:
        def list_tools(self):
            return [{"name": "ZohoCRM_getAccountsRecords"}]

        def call_tool(self, name, arguments):
            calls.append((name, arguments))
            if len(calls) == 1:
                return {
                    "result": {
                        "structuredContent": {
                            "data": {
                                "data": [{"id": "account-1"}],
                                "info": {
                                    "more_records": True,
                                    "next_page_token": "page-2",
                                },
                            }
                        }
                    }
                }
            return {
                "result": {
                    "structuredContent": {
                        "data": {"data": [], "info": {"more_records": False}}
                    }
                }
            }

    requested_servers = []
    monkeypatch.setattr(
        zoho_mcp_client,
        "get_zoho_mcp_client",
        lambda server: requested_servers.append(server) or FakeClient(),
    )

    records = audience._mcp_fetch_accounts()

    assert requested_servers == ["AGENT"]
    assert records == [{"id": "account-1"}]
    assert calls[0][1]["query_params"]["page"] == 1
    assert calls[1][1]["query_params"]["page_token"] == "page-2"


def test_zoho_books_falls_back_to_rest_when_mcp_customer_tool_fails(monkeypatch):
    class FakeBooksClient:
        configured = True

        def list_contacts(self, contact_type):
            assert contact_type == "customer"
            return [
                {
                    "contact_id": "customer-1",
                    "phone": "9876543210",
                    "email": "dealer@example.com",
                }
            ]

    def fail_mcp():
        raise RuntimeError("customer list tool unavailable")

    monkeypatch.setattr(
        audience,
        "_mcp_fetch_books_customers",
        fail_mcp,
    )
    monkeypatch.setattr(
        "app.integrations.zoho_books.get_zoho_client",
        lambda: FakeBooksClient(),
    )

    contacts, error = audience._fetch_zoho_books()

    assert error is None
    assert contacts == [
        {
            "contact_ref": "books_customer-1",
            "msisdn": "919876543210",
            "email": "dealer@example.com",
        }
    ]


def test_zoho_crm_prefers_mcp_accounts_and_falls_back_for_contacts(monkeypatch):
    class FakeCrmClient:
        configured = True

        def list_all_accounts(self):
            raise AssertionError("MCP account fetch should be preferred")

        def list_all_contacts(self):
            return [{"id": "person-1", "Mobile": "9876543210"}]

    monkeypatch.setattr(settings, "k2_zoho_mcp_enabled", True)
    monkeypatch.setattr(
        "app.integrations.zoho_crm.get_zoho_crm_client",
        lambda: FakeCrmClient(),
    )
    monkeypatch.setattr(
        audience,
        "_mcp_fetch_accounts",
        lambda: [{"id": "account-1", "Phone": "9123456789"}],
    )

    def missing_mcp_contacts():
        raise RuntimeError("CRM MCP server has no contacts list tool")

    monkeypatch.setattr(audience, "_mcp_fetch_contacts", missing_mcp_contacts)

    contacts, error = audience._fetch_zoho_crm()

    assert error is None
    assert {contact["contact_ref"] for contact in contacts} == {
        "crm_account-1",
        "crm_contact_person-1",
    }
