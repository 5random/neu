from src.gui.help.navigation import build_help_route_for_settings_anchor, get_help_sections


def _section_by_title(title: str) -> dict[str, str]:
    for section in get_help_sections():
        if section.get('title') == title:
            return section
    raise AssertionError(f'missing help section: {title}')


def test_help_sections_cover_current_settings_workflow() -> None:
    titles = {section.get('title') for section in get_help_sections()}

    assert 'Camera Settings' in titles
    assert 'Measurement & Sessions' in titles
    assert 'E-Mail Notifications' in titles
    assert 'Appearance' in titles
    assert 'Configuration' in titles


def test_email_help_section_describes_static_recipients_and_active_groups() -> None:
    section = _section_by_title('E-Mail Notifications')
    content = str(section.get('content') or '').lower()

    assert 'static recipients' in content
    assert 'system group' in content
    assert 'active groups' in content
    assert 'loads it immediately' in content
    assert 'template' not in content


def test_measurement_help_section_mentions_dashboard_quick_selector_and_group_creation() -> None:
    section = _section_by_title('Measurement & Sessions')
    content = str(section.get('content') or '').lower()

    assert 'dashboard' in content
    assert 'alert counter' in content
    assert 'alert history' in content
    assert 'create them first in **settings > e-mail notifications**'.lower() in content


def test_help_routes_resolve_for_settings_sections() -> None:
    for anchor in ('camera', 'measurement', 'email', 'appearance', 'metadata', 'config', 'update', 'logs'):
        route = build_help_route_for_settings_anchor(anchor)
        assert route is not None
        assert route.startswith('/help?section=')
