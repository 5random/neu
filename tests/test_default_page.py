from src.gui import default_page


def test_dashboard_shows_active_group_selector() -> None:
    assert default_page.SHOW_DASHBOARD_ACTIVE_GROUP_SELECTOR is True


def test_dashboard_shows_alert_history() -> None:
    assert default_page.SHOW_DASHBOARD_ALERT_HISTORY is True


def test_dashboard_shows_alert_stats() -> None:
    assert default_page.SHOW_DASHBOARD_ALERT_STATS is True
