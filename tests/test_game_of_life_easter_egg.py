from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.config import _create_default_config
from src.gui.easter_egg import game_of_life


class _FakeClient:
    def __init__(self, name: str) -> None:
        self.name = name
        self.disconnect_handlers: list[object] = []
        self.layout = _FakeLayout()

    def on_disconnect(self, handler) -> None:
        self.disconnect_handlers.append(handler)


class _FakeLayout:
    def __init__(self) -> None:
        self._event_listeners: dict[str, object] = {}
        self.updated = 0
        self._next_listener_id = 0

    def on(self, event_type: str, handler, *_args, **_kwargs):
        self._next_listener_id += 1
        listener_id = f"listener-{self._next_listener_id}"
        self._event_listeners[listener_id] = SimpleNamespace(id=listener_id, type=event_type, handler=handler)
        return self

    def update(self) -> None:
        self.updated += 1


class _DummyTimer:
    def __init__(self, interval: float, callback) -> None:
        self.interval = interval
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _DummyElement:
    def __init__(self, owner: "_FakeUI", *, text: str | None = None, icon: str | None = None) -> None:
        self.owner = owner
        self.text = text
        self.icon = icon
        self.visible = True
        self.enabled = True
        self.style_calls: list[str] = []
        self.props_calls: list[str] = []
        self.class_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.events: dict[str, object] = {}

    def __enter__(self) -> "_DummyElement":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def classes(self, *args, **kwargs) -> "_DummyElement":
        self.class_calls.append((args, kwargs))
        return self

    def style(self, *args, **_kwargs) -> "_DummyElement":
        if args:
            self.style_calls.append(str(args[0]))
        return self

    def props(self, *args, **_kwargs) -> "_DummyElement":
        if args:
            self.props_calls.append(str(args[0]))
        return self

    def tooltip(self, *_args, **_kwargs) -> "_DummyElement":
        return self

    def on(self, event: str, handler) -> "_DummyElement":
        self.events[event] = handler
        return self

    def update(self) -> "_DummyElement":
        return self

    def enable(self) -> "_DummyElement":
        self.enabled = True
        return self

    def disable(self) -> "_DummyElement":
        self.enabled = False
        return self


class _FakeUI:
    def __init__(self) -> None:
        self.context = SimpleNamespace(client=None)
        self.labels: list[_DummyElement] = []
        self.buttons: list[_DummyElement] = []
        self.head_html_calls: list[str] = []
        self.timers: list[_DummyTimer] = []

    def add_head_html(self, html: str) -> None:
        self.head_html_calls.append(html)

    def timer(self, interval: float, callback, **_kwargs) -> _DummyTimer:
        timer = _DummyTimer(interval, callback)
        self.timers.append(timer)
        return timer

    def emit(self, event: str, args: dict[str, object], *, client: _FakeClient | None = None) -> None:
        payload = SimpleNamespace(args=args)
        target_client = client or self.context.client
        if target_client is None:
            return
        listeners = list(target_client.layout._event_listeners.values())
        for listener in listeners:
            if getattr(listener, "type", None) == event:
                listener.handler(payload)

    def element(self, *_args, **_kwargs) -> _DummyElement:
        return _DummyElement(self)

    def column(self) -> _DummyElement:
        return _DummyElement(self)

    def row(self) -> _DummyElement:
        return _DummyElement(self)

    def label(self, text: str) -> _DummyElement:
        element = _DummyElement(self, text=text)
        self.labels.append(element)
        return element

    def button(self, *args, **kwargs) -> _DummyElement:
        element = _DummyElement(
            self,
            text=args[0] if args and isinstance(args[0], str) else None,
            icon=kwargs.get("icon"),
        )
        on_click = kwargs.get("on_click")
        if callable(on_click):
            element.on("click", on_click)
        self.buttons.append(element)
        return element


class _Surface:
    def __init__(self, *, ready: bool = False) -> None:
        self.ready = ready
        self.snapshots: list[game_of_life.GameOfLifeSnapshot] = []

    def is_stream_ready(self) -> bool:
        return self.ready

    def handle_runtime_snapshot(self, snapshot: game_of_life.GameOfLifeSnapshot) -> bool:
        self.snapshots.append(snapshot)
        return True


@pytest.fixture
def fake_ui(monkeypatch):
    ui = _FakeUI()
    created_clients: list[_FakeClient] = []

    def make_client(name: str = "client") -> _FakeClient:
        client = _FakeClient(name)
        created_clients.append(client)
        ui.context.client = client
        return client

    monkeypatch.setattr(game_of_life, "ui", ui)
    yield ui, make_client

    for client in created_clients:
        game_of_life.reset_game_of_life_runtime_for_tests(client=client)


def test_trigger_detection_only_matches_placeholder_group_name() -> None:
    cfg = _create_default_config()
    cfg.email.active_groups = [game_of_life.CONWAY_EASTER_EGG_GROUP]
    assert game_of_life.is_conway_easter_egg_active(cfg) is True

    cfg.email.active_groups = ["ops", "night"]
    assert game_of_life.is_conway_easter_egg_active(cfg) is False

    cfg.email.active_groups = [f"{game_of_life.CONWAY_EASTER_EGG_GROUP}_copy"]
    assert game_of_life.is_conway_easter_egg_active(cfg) is False


def test_rotate_offsets_rotates_pattern_and_keeps_anchor_positive() -> None:
    rotated = game_of_life.rotate_offsets([(0, 0), (0, 1), (0, 2)], 90)
    assert rotated == [(0, 0), (1, 0), (2, 0)]


def test_runtime_helpers_fail_closed_without_client_context(fake_ui) -> None:
    ui, _make_client = fake_ui
    ui.context.client = None

    assert game_of_life.get_game_of_life_runtime() is None
    assert game_of_life.sync_game_of_life_activation_from_config() is False


def test_runtime_supports_edit_draw_rotate_and_pattern_placement(fake_ui) -> None:
    _ui, make_client = fake_ui
    client = make_client("editor")
    runtime = game_of_life.get_game_of_life_runtime(client=client)
    assert runtime is not None

    runtime.sync_activation(True)
    runtime.clear_board()

    assert runtime.toggle_edit_mode() is True
    assert runtime.apply_draw(4, 5, 1) is True
    assert runtime.snapshot().grid[4][5] == 1

    assert runtime.select_pattern("blinker") is True
    assert runtime.select_pattern("blinker") is True

    snapshot = runtime.snapshot()
    assert snapshot.active_pattern_name == "blinker"
    assert snapshot.active_pattern_rotation == 90

    assert runtime.apply_draw(4, 6, 1) is False
    assert runtime.place_active_pattern(1, 1) is True

    placed = runtime.snapshot().grid
    assert placed[1][1] == 1
    assert placed[2][1] == 1
    assert placed[3][1] == 1


def test_runtime_deactivation_stops_and_reactivation_recreates_fresh_board(monkeypatch, fake_ui) -> None:
    _ui, make_client = fake_ui
    client = make_client("reactivate")
    runtime = game_of_life.get_game_of_life_runtime(client=client)
    assert runtime is not None

    runtime.sync_activation(True)
    runtime.clear_board()
    runtime.toggle_edit_mode()
    runtime.apply_draw(0, 0, 1)
    runtime.toggle_edit_mode()
    runtime.single_step()

    active_snapshot = runtime.snapshot()
    assert active_snapshot.generation == 1
    assert active_snapshot.grid[0][0] == 0

    runtime.sync_activation(False)
    inactive_snapshot = runtime.snapshot()
    assert inactive_snapshot.active is False
    assert inactive_snapshot.generation == 0
    assert inactive_snapshot.is_running is False
    assert sum(sum(row) for row in inactive_snapshot.grid) == 0

    monkeypatch.setattr(game_of_life.random, "randint", lambda _a, _b: 1)
    runtime.sync_activation(True)
    reactivated = runtime.snapshot()
    assert reactivated.active is True
    assert reactivated.generation == 0
    assert reactivated.is_running is False
    assert all(cell == 1 for row in reactivated.grid for cell in row)


def test_compute_overlay_visibility_requires_stream_ready_and_trigger() -> None:
    assert game_of_life.compute_overlay_visibility(stream_ready=True, trigger_active=True) is True
    assert game_of_life.compute_overlay_visibility(stream_ready=True, trigger_active=False) is False
    assert game_of_life.compute_overlay_visibility(stream_ready=False, trigger_active=True) is False


def test_runtime_isolated_per_client_and_sync_updates_only_current_client(monkeypatch, fake_ui) -> None:
    _ui, make_client = fake_ui
    cfg = _create_default_config()
    cfg.email.active_groups = [game_of_life.CONWAY_EASTER_EGG_GROUP]
    monkeypatch.setattr(game_of_life, "get_global_config", lambda: cfg)

    client_a = make_client("a")
    client_b = make_client("b")
    runtime_a = game_of_life.get_game_of_life_runtime(client=client_a)
    runtime_b = game_of_life.get_game_of_life_runtime(client=client_b)
    assert runtime_a is not None
    assert runtime_b is not None
    assert runtime_a is not runtime_b

    assert runtime_a.snapshot().active is False
    assert runtime_b.snapshot().active is False

    game_of_life.sync_game_of_life_activation_from_config(client=client_a)

    assert runtime_a.snapshot().active is True
    assert runtime_b.snapshot().active is False


def test_runtime_heartbeat_is_lazy_and_disconnect_cleanup_stops_it(fake_ui) -> None:
    ui, make_client = fake_ui
    client = make_client("heartbeat")
    runtime = game_of_life.get_game_of_life_runtime(client=client)
    assert runtime is not None
    assert client.disconnect_handlers

    surface = _Surface(ready=False)
    runtime.register_surface(surface)

    runtime.sync_activation(True)
    assert ui.timers == []

    surface.ready = True
    runtime.handle_surface_stream_phase_change(surface)
    assert ui.timers == []

    runtime.toggle_running()
    assert len(ui.timers) == 1
    assert ui.timers[0].cancelled is False

    runtime.toggle_running()
    assert ui.timers[0].cancelled is True

    assert getattr(client, "cvd_game_of_life_runtime", None) is runtime

    disconnect_handler = client.disconnect_handlers[0]
    disconnect_handler()

    assert getattr(client, "cvd_game_of_life_runtime", None) is None


def test_dashboard_surface_removes_global_layout_listeners_when_closed(fake_ui) -> None:
    _ui, make_client = fake_ui
    client = make_client("listeners")

    binder = game_of_life.create_dashboard_game_layer(
        stream_host_id="dashboard-stream",
        controls_host_id="dashboard-controls",
    )

    assert len(client.layout._event_listeners) == 2

    binder._mark_closed()

    assert client.layout._event_listeners == {}


def test_dashboard_controls_require_trigger_group_and_ready_stream(monkeypatch, fake_ui) -> None:
    ui, make_client = fake_ui
    cfg = _create_default_config()
    cfg.email.active_groups = []
    monkeypatch.setattr(game_of_life, "get_global_config", lambda: cfg)

    client = make_client("dashboard")
    binder = game_of_life.create_dashboard_game_layer(
        stream_host_id="dashboard-stream",
        controls_host_id="dashboard-controls",
    )
    binder.build_controls()

    game_of_life.sync_game_of_life_activation_from_config(client=client)
    ui.emit(
        game_of_life.STREAM_PHASE_EVENT,
        {"host_id": "dashboard-stream", "phase": "ready", "ready": True},
    )
    assert binder.overlay_root.visible is False
    assert binder.controls_host is not None
    assert binder.controls_host.visible is False

    cfg.email.active_groups = [game_of_life.CONWAY_EASTER_EGG_GROUP]
    game_of_life.sync_game_of_life_activation_from_config(client=client)
    assert binder.overlay_root.visible is True
    assert binder.controls_host.visible is True

    ui.emit(
        game_of_life.STREAM_PHASE_EVENT,
        {"host_id": "dashboard-stream", "phase": "paused", "ready": False},
    )
    assert binder.overlay_root.visible is False
    assert binder.controls_host.visible is False


def test_passive_settings_overlay_renders_only_when_stream_is_ready(monkeypatch, fake_ui) -> None:
    ui, make_client = fake_ui
    cfg = _create_default_config()
    cfg.email.active_groups = [game_of_life.CONWAY_EASTER_EGG_GROUP]
    monkeypatch.setattr(game_of_life, "get_global_config", lambda: cfg)

    client = make_client("settings")
    changes: list[str] = []
    controller = game_of_life.create_passive_game_layer(
        stream_host_id="settings-stream",
        on_change=lambda: changes.append("change"),
    )
    runtime = game_of_life.get_game_of_life_runtime(client=client)
    assert runtime is not None

    game_of_life.sync_game_of_life_activation_from_config(client=client)
    runtime.clear_board()
    runtime.toggle_edit_mode()
    runtime.apply_draw(0, 0, 1)

    assert controller.render_svg_fragments(480, 320) == []

    ui.emit(
        game_of_life.STREAM_PHASE_EVENT,
        {"host_id": "settings-stream", "phase": "ready", "ready": True},
    )

    fragments = controller.render_svg_fragments(480, 320)
    assert any("<rect " in fragment for fragment in fragments)
    assert changes
