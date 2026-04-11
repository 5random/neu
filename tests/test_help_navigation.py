from pathlib import Path

from src.gui.help import navigation as navigation_module
from src.gui.help.navigation import build_help_route_for_settings_anchor, get_help_sections


def _section_by_title(title: str) -> dict[str, str]:
    for section in get_help_sections():
        if section.get('title') == title:
            return section
    raise AssertionError(f'missing help section: {title}')


def _cleanup_test_root(path: Path) -> None:
    for child in sorted(path.rglob('*'), reverse=True):
        try:
            if child.is_dir():
                child.rmdir()
            else:
                child.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        path.rmdir()
    except OSError:
        pass


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
    assert 'managed in the e-mail section' in content


def test_help_routes_resolve_for_settings_sections() -> None:
    for anchor in ('camera', 'measurement', 'email', 'appearance', 'metadata', 'config', 'update', 'logs'):
        route = build_help_route_for_settings_anchor(anchor)
        assert route is not None
        assert route.startswith('/help?section=')


def test_help_navigation_uses_root_help_yaml_path() -> None:
    expected_path = navigation_module._find_project_root() / 'help' / 'help.yaml'

    assert navigation_module._HELP_YAML_PATH == expected_path
    assert navigation_module._HELP_YAML_PATH.exists()


def test_find_project_root_prefers_pyproject_marker(monkeypatch) -> None:
    repo_root = Path('.pytest_help_navigation_root_pyproject') / 'repo'
    fake_module = repo_root / 'src' / 'gui' / 'help' / 'navigation.py'
    fake_module.parent.mkdir(parents=True, exist_ok=True)
    fake_module.write_text('# stub\n', encoding='utf-8')
    (repo_root / 'pyproject.toml').write_text('[tool.pytest]\n', encoding='utf-8')

    try:
        monkeypatch.setattr(navigation_module, '__file__', str(fake_module))
        assert navigation_module._find_project_root() == repo_root.resolve()
    finally:
        _cleanup_test_root(repo_root.parent)


def test_find_project_root_falls_back_to_git_marker(monkeypatch) -> None:
    repo_root = Path('.pytest_help_navigation_root_git') / 'repo'
    fake_module = repo_root / 'src' / 'gui' / 'help' / 'navigation.py'
    fake_module.parent.mkdir(parents=True, exist_ok=True)
    fake_module.write_text('# stub\n', encoding='utf-8')
    (repo_root / '.git').mkdir(exist_ok=True)
    path_cls = type(repo_root.resolve())
    original_exists = path_cls.exists

    try:
        monkeypatch.setattr(navigation_module, '__file__', str(fake_module))
        fake_git_marker = (repo_root / '.git').resolve()

        def fake_exists(self) -> bool:
            resolved = self.resolve(strict=False)
            if resolved.name == 'pyproject.toml':
                return False
            if resolved.name == '.git':
                return resolved == fake_git_marker
            return original_exists(self)

        monkeypatch.setattr(path_cls, 'exists', fake_exists)
        assert navigation_module._find_project_root() == repo_root.resolve()
    finally:
        _cleanup_test_root(repo_root.parent)


def test_find_project_root_uses_legacy_depth_without_markers(monkeypatch) -> None:
    repo_root = Path('.pytest_help_navigation_root_legacy') / 'repo'
    fake_module = repo_root / 'src' / 'gui' / 'help' / 'navigation.py'
    fake_module.parent.mkdir(parents=True, exist_ok=True)
    fake_module.write_text('# stub\n', encoding='utf-8')
    path_cls = type(repo_root.resolve())
    original_exists = path_cls.exists

    try:
        monkeypatch.setattr(navigation_module, '__file__', str(fake_module))
        def fake_exists(self) -> bool:
            resolved = self.resolve(strict=False)
            if resolved.name in {'pyproject.toml', '.git'}:
                return False
            return original_exists(self)

        monkeypatch.setattr(path_cls, 'exists', fake_exists)
        assert navigation_module._find_project_root() == fake_module.resolve().parents[3]
    finally:
        _cleanup_test_root(repo_root.parent)


def test_load_help_content_falls_back_when_root_help_yaml_is_missing(monkeypatch) -> None:
    missing_path = Path('.pytest_local_help_missing') / 'missing-help.yaml'
    monkeypatch.setattr(navigation_module, '_HELP_YAML_PATH', missing_path)
    navigation_module._load_help_content_cached.cache_clear()
    navigation_module._get_help_sections_cached.cache_clear()

    payload = navigation_module.load_help_content()

    assert payload == {'help': {'title': 'Help', 'sections': []}}

    navigation_module._load_help_content_cached.cache_clear()
    navigation_module._get_help_sections_cached.cache_clear()
