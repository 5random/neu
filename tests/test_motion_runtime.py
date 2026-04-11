from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from src.gui import motion_runtime


class _CameraStub:
    def __init__(self) -> None:
        self.enabled_callbacks: list[object] = []
        self.disabled_callbacks: list[object] = []

    def enable_motion_detection(self, callback) -> None:
        self.enabled_callbacks.append(callback)

    def disable_motion_detection(self, callback) -> None:
        self.disabled_callbacks.append(callback)


class _DisconnectClient:
    def __init__(self) -> None:
        self.disconnect_handlers: list[object] = []

    def on_disconnect(self, handler) -> None:
        self.disconnect_handlers.append(handler)


class _FailingDisconnectClient:
    def on_disconnect(self, handler) -> None:
        raise RuntimeError(f'boom: {handler}')


class _FakeApp:
    def __init__(self) -> None:
        self.routes: list[tuple[str, object]] = []

    def get(self, path: str):
        def _decorator(fn):
            self.routes.append((path, fn))
            return fn
        return _decorator


class _FailingApp:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, _path: str):
        def _decorator(fn):
            self.calls += 1
            raise RuntimeError(f'boom: {fn}')
        return _decorator


def test_register_combined_motion_listener_tolerates_client_without_on_disconnect() -> None:
    camera = _CameraStub()
    client = SimpleNamespace()

    result = motion_runtime.register_combined_motion_listener(
        camera,
        client=client,
        callback=lambda *_args: None,
        cleanup_attr_name='cleanup_attr',
        disconnect_attr_name='disconnect_attr',
        logger=logging.getLogger('test.motion_runtime'),
    )

    assert result is True
    assert len(camera.enabled_callbacks) == 1
    assert callable(getattr(client, 'cleanup_attr'))
    assert not hasattr(client, 'disconnect_attr')

    getattr(client, 'cleanup_attr')()
    assert camera.disabled_callbacks == [camera.enabled_callbacks[0]]


def test_register_combined_motion_listener_does_not_store_disconnect_attr_when_registration_fails() -> None:
    camera = _CameraStub()
    client = _FailingDisconnectClient()

    result = motion_runtime.register_combined_motion_listener(
        camera,
        client=client,
        callback=lambda *_args: None,
        cleanup_attr_name='cleanup_attr',
        disconnect_attr_name='disconnect_attr',
        logger=logging.getLogger('test.motion_runtime'),
    )

    assert result is True
    assert len(camera.enabled_callbacks) == 1
    assert callable(getattr(client, 'cleanup_attr'))
    assert not hasattr(client, 'disconnect_attr')

    getattr(client, 'cleanup_attr')()
    assert camera.disabled_callbacks == [camera.enabled_callbacks[0]]


def test_register_combined_motion_listener_stores_disconnect_attr_after_successful_registration() -> None:
    camera = _CameraStub()
    client = _DisconnectClient()

    result = motion_runtime.register_combined_motion_listener(
        camera,
        client=client,
        callback=lambda *_args: None,
        cleanup_attr_name='cleanup_attr',
        disconnect_attr_name='disconnect_attr',
        logger=logging.getLogger('test.motion_runtime'),
    )

    assert result is True
    assert len(client.disconnect_handlers) == 1
    assert getattr(client, 'disconnect_attr') is client.disconnect_handlers[0]

    client.disconnect_handlers[0]()
    assert camera.disabled_callbacks == [camera.enabled_callbacks[0]]
    assert not hasattr(client, 'cleanup_attr')
    assert not hasattr(client, 'disconnect_attr')


def test_motion_update_authorization_accepts_valid_bearer_and_api_key(monkeypatch) -> None:
    monkeypatch.setenv('CVD_API_TOKEN', 'secret-bearer')
    monkeypatch.setenv('CVD_API_KEY', 'secret-key')

    bearer_request = SimpleNamespace(headers={'authorization': 'Bearer secret-bearer'})
    api_key_request = SimpleNamespace(headers={'x-api-key': 'secret-key'})

    assert motion_runtime._is_motion_update_authorized(bearer_request) == (True, 'ok')
    assert motion_runtime._is_motion_update_authorized(api_key_request) == (True, 'ok')


def test_motion_update_authorization_rejects_invalid_credentials(monkeypatch) -> None:
    monkeypatch.setenv('CVD_API_TOKEN', 'secret-bearer')
    monkeypatch.setenv('CVD_API_KEY', 'secret-key')

    request = SimpleNamespace(headers={'authorization': 'Bearer wrong', 'x-api-key': 'wrong'})

    assert motion_runtime._is_motion_update_authorized(request) == (False, 'invalid_credentials')


def test_ensure_motion_update_route_registered_only_registers_once(monkeypatch) -> None:
    fake_app = _FakeApp()

    monkeypatch.setattr(motion_runtime, 'app', fake_app)
    monkeypatch.setattr(motion_runtime, '_api_route_registered', False)

    motion_runtime._ensure_motion_update_route_registered()
    motion_runtime._ensure_motion_update_route_registered()

    assert len(fake_app.routes) == 1
    assert fake_app.routes[0][0] == '/api/motion/update'
    assert motion_runtime._api_route_registered is True


def test_ensure_motion_update_route_registered_allows_retry_after_failed_registration(monkeypatch) -> None:
    failing_app = _FailingApp()

    monkeypatch.setattr(motion_runtime, 'app', failing_app)
    monkeypatch.setattr(motion_runtime, '_api_route_registered', False)

    with pytest.raises(RuntimeError):
        motion_runtime._ensure_motion_update_route_registered()

    assert failing_app.calls == 1
    assert motion_runtime._api_route_registered is False

    fake_app = _FakeApp()
    monkeypatch.setattr(motion_runtime, 'app', fake_app)

    motion_runtime._ensure_motion_update_route_registered()

    assert len(fake_app.routes) == 1
    assert motion_runtime._api_route_registered is True
