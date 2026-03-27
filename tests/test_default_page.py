from src.gui import default_page


def test_dashboard_shows_active_group_selector() -> None:
    assert default_page.SHOW_DASHBOARD_ACTIVE_GROUP_SELECTOR is True
