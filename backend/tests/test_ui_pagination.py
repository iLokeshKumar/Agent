from fasthtml.common import to_xml

from app.api.ui import _page_controls, _page_window


def test_page_window_clamps_invalid_and_out_of_range_pages():
    assert _page_window(0, 23) == (1, 3, 0)
    assert _page_window(2, 23) == (2, 3, 10)
    assert _page_window(9, 23) == (3, 3, 20)


def test_page_controls_keep_filters_and_disable_boundary_actions():
    html = to_xml(_page_controls(
        "/htmx/tasks", 2, 3, 23, "#tasks-list", "#tasks-filter"
    ))

    assert "Page 2 of 3" in html
    assert "page=1" in html
    assert "page=3" in html
    assert 'hx-include="#tasks-filter"' in html
    assert 'hx-target="#tasks-list"' in html


def test_page_controls_omit_empty_filter_attribute():
    html = to_xml(_page_controls(
        "/htmx/agents", 1, 3, 25, "#agents-list"
    ))

    assert "hx-include" not in html
    assert "disabled" in html
