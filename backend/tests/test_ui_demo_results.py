from fasthtml.common import to_xml

from app.api.ui import _format_result


def _render(task_type, output):
    return to_xml(_format_result({"output": output}, task_type))


def test_collections_result_uses_plain_language_and_draft_disclaimer():
    html = _render("collections", {
        "summary": {
            "total_invoices": 2,
            "total_overdue": 2,
            "total_outstanding": 1500,
            "oldest_overdue_days": 69,
            "follow_up_action": "REMINDER",
        },
        "aging_summary": {
            "days_61_90": {"count": 1, "total": 900},
        },
        "processed_invoices": [{
            "customer": "Galaxy Mart",
            "invoice_number": "INV-301",
            "outstanding_amount": 900,
            "days_overdue": 69,
        }],
    })

    assert "2 of 2 invoices are overdue" in html
    assert "Customers to follow up" in html
    assert "No message has been sent" in html
    assert "aging_summary" not in html


def test_books_to_tally_explains_staging_and_reconciliation():
    html = _render("books_to_tally", {
        "summary": {
            "total_entries": 3,
            "total_debit": 1200,
            "total_credit": 1200,
            "balanced": True,
            "unmapped_count": 0,
            "period": "October 2026",
        },
    })

    assert "3 journal entries" in html
    assert "Balanced" in html
    assert "Nothing has been posted to Tally" in html


def test_gsm_result_summarizes_dealer_portfolio_by_branch():
    html = _render("gsm_command", {
        "entity": "YEXIS-EL",
        "as_of": "2026-10-07",
        "total_dealers": 4,
        "total_outstanding": 50000,
        "branches": {
            "Chennai": {
                "dealer_count": 4,
                "overdue_count": 1,
                "total_outstanding": 50000,
            },
        },
    })

    assert "4 dealers" in html
    assert "By branch" in html
    assert "read-only management summary" in html


def test_purchase_result_explains_expired_zoho_trial():
    html = to_xml(_format_result({
        "error": (
            'Zoho Books 400: {"code":103001,"message":"Your trial is over. '
            'Please upgrade to a paid plan."}'
        ),
    }, "purchase_request"))

    assert "account trial has ended" in html
    assert "did not create the purchase order" in html
    assert "103001" not in html
