"""One site for a desk computer, a tablet and a phone.

Layout is proved in a browser, not here: every page was loaded at 320, 375, 768, 1024 and 1280 px
and none scrolls sideways (the widths are in ``docs/ha.md``). What these tests pin is what makes
that true and is easy to lose in an edit: every page declares a device-width viewport and carries
the shared responsive rules, and every side-by-side panel is a ``.grid`` - the one thing those
rules know how to stack.
"""

from __future__ import annotations

import re

import pytest

from cdi_adapter.webapp import (
    admin_page,
    auth,
    c360_page,
    conflict_page,
    monitor_page,
    page,
    result_page,
    review_page,
    reviewer_page,
    theme,
)

VIEWPORT = '<meta name=viewport content="width=device-width,initial-scale=1">'

PAGES = {
    "upload": page.upload_page,
    "result": lambda: result_page.result_page("00000000-0000-0000-0000-000000000000"),
    "conflicts": conflict_page.conflicts_page,
    "review": review_page.review_page,
    "reviewer": reviewer_page.reviewer_page,
    "c360": c360_page.c360_page,
    "login": auth.login_page,
    "admin": lambda: admin_page.admin_page(
        {"source_document": 1}, table=None, rows=None, mpi=None, graph=None
    ),  # fmt: skip
}


@pytest.mark.parametrize("name", sorted(PAGES))
def test_every_page_fits_the_device_and_carries_the_shared_rules(name):
    html = PAGES[name]()
    assert VIEWPORT in html
    assert theme.RESPONSIVE in html


def test_the_monitor_microsite_fits_the_device_too():
    html = monitor_page.monitor_page()
    assert VIEWPORT in html and "@media (max-width:1100px)" in html


@pytest.mark.parametrize("name", sorted(PAGES))
def test_every_side_by_side_panel_is_a_grid_the_rules_can_stack(name):
    """An inline two-column layout that is not a ``.grid`` would stay two columns on a phone."""
    html = PAGES[name]()
    for tag in re.findall(r"<[a-z]+[^>]*grid-template-columns[^>]*>", html):
        assert "class=grid" in tag or 'class="grid"' in tag, tag


def test_panels_stack_and_the_inline_columns_cannot_win():
    css = theme.RESPONSIVE
    assert "@media (max-width:900px){.grid{grid-template-columns:minmax(0,1fr)!important}}" in css


def test_the_header_wraps_and_its_links_scroll_on_a_phone():
    css = theme.RESPONSIVE
    assert "header.top{flex-wrap:wrap}" in css
    phone = css[css.index("@media (max-width:760px)") :]
    assert "header.top nav{order:3;flex:0 0 100%" in phone and "overflow-x:auto" in phone


def test_a_wide_table_scrolls_inside_itself_not_the_page():
    css = theme.RESPONSIVE
    assert ".card{overflow-x:auto}" in css and ".grid>*{min-width:0}" in css
    assert "table{display:block;max-width:100%;overflow-x:auto}" in css


def test_controls_are_finger_sized_and_fields_do_not_trigger_ios_zoom():
    phone = theme.RESPONSIVE[theme.RESPONSIVE.index("@media (max-width:760px)") :]
    assert "min-height:42px" in phone
    assert "select,textarea{font-size:16px}" in phone


def test_the_desktop_stylesheet_is_still_all_there():
    """The rules are added after the existing ones; nothing a desk screen used was taken out."""
    assert theme.CSS.endswith(theme.RESPONSIVE)
    for rule in ("main{padding:20px;max-width:1280px;margin:0 auto}", ".grid{display:grid",
                 "header.top nav{margin-left:auto;display:flex;gap:14px}"):  # fmt: skip
        assert rule in theme.CSS
