from types import SimpleNamespace

from src.gui import gui_


class _FakeClient:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    def run_javascript(self, code: str) -> None:
        self.scripts.append(code)


def test_refresh_connected_clients_targets_single_client() -> None:
    client = _FakeClient()

    gui_.refresh_connected_clients(client=client, delay_ms=250)

    assert client.scripts == ['window.setTimeout(() => window.location.reload(), 250);']


def test_refresh_connected_clients_broadcasts_to_all_clients(monkeypatch) -> None:
    client_a = _FakeClient()
    client_b = _FakeClient()
    fake_app = SimpleNamespace(clients={'a': client_a, 'b': client_b})
    monkeypatch.setattr(gui_, 'app', fake_app)

    gui_.refresh_connected_clients(broadcast=True, delay_ms=125)

    expected = 'window.setTimeout(() => window.location.reload(), 125);'
    assert client_a.scripts == [expected]
    assert client_b.scripts == [expected]


def test_build_post_restart_redirect_script_targets_dashboard() -> None:
    script = gui_.build_post_restart_redirect_script(
        marker_key='cvd.app_restart.pending_redirect',
        target_route='/',
    )

    assert '"cvd.app_restart.pending_redirect"' in script
    assert 'window.sessionStorage.getItem(markerKey) === \'pending\'' in script
    assert 'window.location.replace(targetRoute);' in script
    assert 'window.sessionStorage.setItem(markerKey, \'pending\');' in script
