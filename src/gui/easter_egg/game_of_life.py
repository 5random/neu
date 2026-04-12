from __future__ import annotations

from dataclasses import dataclass
import random
import threading
import time
from typing import Any, Callable, Optional

from nicegui import ui

from src.config import get_global_config, get_logger
from src.gui.util import (
    is_deleted_parent_slot_error,
    register_client_disconnect_handler,
)

logger = get_logger("gui.easter_egg.conway")

CONWAY_EASTER_EGG_GROUP = "kv-gol"

GRID_ROWS = 32
GRID_COLS = 48
UPDATE_INTERVAL = 0.12
STREAM_READY_PHASE = "ready"
STREAM_PHASE_EVENT = "cvd_gol_stream_phase"
_RUNTIME_ATTR = "cvd_game_of_life_runtime"
_RUNTIME_DISCONNECT_ATTR = "cvd_game_of_life_runtime_disconnect_handler"
_ALL_GRID_CELLS = tuple((row, col) for row in range(GRID_ROWS) for col in range(GRID_COLS))

PATTERNS: dict[str, dict[str, object]] = {
    "blinker": {
        "icon": "more_horiz",
        "tooltip": "Blinker",
        "cells": [(0, 0), (0, 1), (0, 2)],
    },
    "glider": {
        "icon": "north_east",
        "tooltip": "Glider",
        "cells": [(0, 1), (1, 2), (2, 0), (2, 1), (2, 2)],
    },
    "beacon": {
        "icon": "grid_view",
        "tooltip": "Beacon",
        "cells": [(0, 0), (0, 1), (1, 0), (1, 1), (2, 2), (2, 3), (3, 2), (3, 3)],
    },
    "toad": {
        "icon": "drag_indicator",
        "tooltip": "Toad",
        "cells": [(0, 1), (0, 2), (0, 3), (1, 0), (1, 1), (1, 2)],
    },
    "lwss": {
        "icon": "flight",
        "tooltip": "Lightweight spaceship",
        "cells": [(0, 1), (0, 4), (1, 0), (2, 0), (2, 4), (3, 0), (3, 1), (3, 2), (3, 3)],
    },
}


@dataclass(frozen=True)
class GameOfLifeSnapshot:
    active: bool
    version: int
    generation: int
    is_running: bool
    is_edit_mode: bool
    active_pattern_name: str | None
    active_pattern_rotation: int
    grid: tuple[tuple[int, ...], ...]
    dirty_cells: tuple[tuple[int, int], ...]
    full_redraw: bool


class GameOfLife:
    """Manage Conway's Game of Life state."""

    def __init__(self, rows: int, cols: int) -> None:
        self.rows = rows
        self.cols = cols
        self.grid: list[list[int]] = []
        self.randomize()

    def randomize(self) -> None:
        self.grid = [
            [random.randint(0, 1) for _ in range(self.cols)]
            for _ in range(self.rows)
        ]

    def clear(self) -> None:
        self.grid = [[0 for _ in range(self.cols)] for _ in range(self.rows)]

    def set_cell(self, row: int, col: int, value: int) -> bool:
        normalized = 1 if value else 0
        if self.grid[row][col] == normalized:
            return False
        self.grid[row][col] = normalized
        return True

    def count_neighbors(self, row: int, col: int) -> int:
        count = 0
        r0 = max(0, row - 1)
        r1 = min(self.rows - 1, row + 1)
        c0 = max(0, col - 1)
        c1 = min(self.cols - 1, col + 1)

        for r in range(r0, r1 + 1):
            row_data = self.grid[r]
            for c in range(c0, c1 + 1):
                if r == row and c == col:
                    continue
                count += row_data[c]
        return count

    def next_generation(self) -> list[tuple[int, int]]:
        old_grid = self.grid
        new_grid = [[0] * self.cols for _ in range(self.rows)]
        changed: list[tuple[int, int]] = []

        for r in range(self.rows):
            old_row = old_grid[r]
            new_row = new_grid[r]
            for c in range(self.cols):
                neighbors = self.count_neighbors(r, c)
                alive = old_row[c] == 1
                if alive:
                    new_value = 1 if neighbors in (2, 3) else 0
                else:
                    new_value = 1 if neighbors == 3 else 0
                new_row[c] = new_value
                if new_value != old_row[c]:
                    changed.append((r, c))

        self.grid = new_grid
        return changed


def rotate_offsets(offsets: list[tuple[int, int]], rotation: int) -> list[tuple[int, int]]:
    rot = rotation % 360
    result: list[tuple[int, int]] = []

    for row, col in offsets:
        if rot == 0:
            next_row, next_col = row, col
        elif rot == 90:
            next_row, next_col = col, -row
        elif rot == 180:
            next_row, next_col = -row, -col
        elif rot == 270:
            next_row, next_col = -col, row
        else:
            next_row, next_col = row, col
        result.append((next_row, next_col))

    min_row = min(r for r, _ in result)
    min_col = min(c for _, c in result)
    return [(row - min_row, col - min_col) for row, col in result]


class GameOfLifeRuntime:
    """Client-local Conway runtime shared by dashboard and settings in one tab."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.rows = GRID_ROWS
        self.cols = GRID_COLS
        self.update_interval = UPDATE_INTERVAL
        self._lock = threading.RLock()
        self._game = GameOfLife(self.rows, self.cols)
        self._active = False
        self._is_running = False
        self._is_edit_mode = False
        self._generation = 0
        self._active_pattern_name: str | None = None
        self._active_pattern_rotation = 0
        self._version = 0
        self._last_tick_monotonic = 0.0
        self._dirty_cells: set[tuple[int, int]] = set(_ALL_GRID_CELLS)
        self._full_redraw = True
        self._heartbeat_timer: Any | None = None
        self._surfaces: list[Any] = []
        self._game.clear()

    def snapshot(self, *, force_full_redraw: bool = False) -> GameOfLifeSnapshot:
        with self._lock:
            return self._build_snapshot_locked(force_full_redraw=force_full_redraw)

    def sync_activation(self, active: bool) -> bool:
        snapshot: GameOfLifeSnapshot | None = None
        with self._lock:
            if active != self._active:
                if active:
                    self._activate_locked()
                else:
                    self._deactivate_locked()
                snapshot = self._build_snapshot_locked()
                self._clear_dirty_locked()
            current_active = self._active
        if snapshot is not None:
            self._broadcast_snapshot(snapshot)
        self._sync_heartbeat()
        return current_active

    def toggle_running(self) -> bool:
        snapshot: GameOfLifeSnapshot | None = None
        with self._lock:
            if not self._active:
                return False
            if self._is_running:
                self._is_running = False
            else:
                self._is_edit_mode = False
                self._active_pattern_name = None
                self._active_pattern_rotation = 0
                self._is_running = True
                self._last_tick_monotonic = time.monotonic()
            self._touch_locked()
            snapshot = self._build_snapshot_locked()
            self._clear_dirty_locked()
            current_running = self._is_running
        self._broadcast_snapshot(snapshot)
        self._sync_heartbeat()
        return current_running

    def single_step(self) -> bool:
        snapshot: GameOfLifeSnapshot | None = None
        with self._lock:
            if not self._active or self._is_running:
                return False
            changed = self._game.next_generation()
            self._generation += 1
            self._touch_locked(dirty_cells=changed)
            snapshot = self._build_snapshot_locked()
            self._clear_dirty_locked()
        self._broadcast_snapshot(snapshot)
        return True

    def toggle_edit_mode(self) -> bool:
        snapshot: GameOfLifeSnapshot | None = None
        with self._lock:
            if not self._active or self._is_running:
                return False
            self._is_edit_mode = not self._is_edit_mode
            if not self._is_edit_mode:
                self._active_pattern_name = None
                self._active_pattern_rotation = 0
            self._touch_locked()
            snapshot = self._build_snapshot_locked()
            self._clear_dirty_locked()
            current_edit_mode = self._is_edit_mode
        self._broadcast_snapshot(snapshot)
        return current_edit_mode

    def select_pattern(self, pattern_name: str) -> bool:
        snapshot: GameOfLifeSnapshot | None = None
        with self._lock:
            if (
                not self._active
                or not self._is_edit_mode
                or self._is_running
                or pattern_name not in PATTERNS
            ):
                return False
            if self._active_pattern_name == pattern_name:
                self._active_pattern_rotation = (self._active_pattern_rotation + 90) % 360
            else:
                self._active_pattern_name = pattern_name
                self._active_pattern_rotation = 0
            self._touch_locked()
            snapshot = self._build_snapshot_locked()
            self._clear_dirty_locked()
        self._broadcast_snapshot(snapshot)
        return True

    def randomize_board(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._reset_board_locked(randomize=True)
            snapshot = self._build_snapshot_locked()
            self._clear_dirty_locked()
        self._broadcast_snapshot(snapshot)

    def clear_board(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._reset_board_locked(randomize=False)
            snapshot = self._build_snapshot_locked()
            self._clear_dirty_locked()
        self._broadcast_snapshot(snapshot)

    def apply_draw(self, row: int, col: int, value: int) -> bool:
        with self._lock:
            if (
                not self._active
                or not self._is_edit_mode
                or self._is_running
                or self._active_pattern_name is not None
            ):
                return False
            changed = self._game.set_cell(row, col, value)
            if not changed:
                return False
            self._touch_locked(dirty_cells=[(row, col)])
            snapshot = self._build_snapshot_locked()
            self._clear_dirty_locked()
        self._broadcast_snapshot(snapshot)
        return True

    def place_active_pattern(self, anchor_row: int, anchor_col: int) -> bool:
        with self._lock:
            if (
                not self._active
                or not self._is_edit_mode
                or self._is_running
                or self._active_pattern_name is None
            ):
                return False
            changed_cells: list[tuple[int, int]] = []
            for row, col in self._compute_active_pattern_cells_locked(anchor_row, anchor_col):
                if self._game.set_cell(row, col, 1):
                    changed_cells.append((row, col))
            if not changed_cells:
                return False
            self._touch_locked(dirty_cells=changed_cells)
            snapshot = self._build_snapshot_locked()
            self._clear_dirty_locked()
        self._broadcast_snapshot(snapshot)
        return True

    def compute_active_pattern_cells(self, anchor_row: int, anchor_col: int) -> list[tuple[int, int]]:
        with self._lock:
            return list(self._compute_active_pattern_cells_locked(anchor_row, anchor_col))

    def register_surface(self, surface: Any) -> None:
        with self._lock:
            if surface not in self._surfaces:
                self._surfaces.append(surface)
            snapshot = self._build_snapshot_locked(force_full_redraw=True)
        if not self._deliver_snapshot(surface, snapshot):
            self.unregister_surface(surface)
            return
        self._sync_heartbeat()

    def unregister_surface(self, surface: Any) -> None:
        with self._lock:
            self._surfaces = [item for item in self._surfaces if item is not surface]
        self._sync_heartbeat()

    def handle_surface_stream_phase_change(self, surface: Any) -> None:
        with self._lock:
            if surface not in self._surfaces:
                return
            snapshot = self._build_snapshot_locked(force_full_redraw=True)
        if not self._deliver_snapshot(surface, snapshot):
            self.unregister_surface(surface)
            return
        self._sync_heartbeat()

    def shutdown(self) -> None:
        with self._lock:
            timer = self._heartbeat_timer
            self._heartbeat_timer = None
            self._surfaces = []
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                logger.debug("Failed to cancel Conway heartbeat during shutdown", exc_info=True)

    def _heartbeat_tick(self) -> None:
        with self._lock:
            if not self._should_run_heartbeat_locked():
                timer = self._heartbeat_timer
                self._heartbeat_timer = None
                if timer is not None:
                    try:
                        timer.cancel()
                    except Exception:
                        logger.debug("Failed to cancel inactive Conway heartbeat", exc_info=True)
                return
            now_monotonic = time.monotonic()
            if (
                not self._active
                or not self._is_running
                or (now_monotonic - self._last_tick_monotonic) < self.update_interval
            ):
                return
            changed = self._game.next_generation()
            self._generation += 1
            self._last_tick_monotonic = now_monotonic
            self._touch_locked(dirty_cells=changed)
            snapshot = self._build_snapshot_locked()
            self._clear_dirty_locked()
        self._broadcast_snapshot(snapshot)

    def _activate_locked(self) -> None:
        self._active = True
        self._reset_board_locked(randomize=True)

    def _deactivate_locked(self) -> None:
        self._active = False
        self._reset_board_locked(randomize=False)

    def _reset_board_locked(self, *, randomize: bool) -> None:
        self._is_running = False
        self._is_edit_mode = False
        self._generation = 0
        self._active_pattern_name = None
        self._active_pattern_rotation = 0
        self._last_tick_monotonic = 0.0
        if randomize:
            self._game.randomize()
        else:
            self._game.clear()
        self._touch_locked(full_redraw=True)

    def _touch_locked(
        self,
        *,
        dirty_cells: Optional[list[tuple[int, int]]] = None,
        full_redraw: bool = False,
    ) -> None:
        self._version += 1
        if full_redraw:
            self._dirty_cells = set(_ALL_GRID_CELLS)
            self._full_redraw = True
            return
        if dirty_cells:
            self._dirty_cells.update(dirty_cells)

    def _build_snapshot_locked(self, *, force_full_redraw: bool = False) -> GameOfLifeSnapshot:
        full_redraw = force_full_redraw or self._full_redraw
        dirty_cells = _ALL_GRID_CELLS if full_redraw else tuple(sorted(self._dirty_cells))
        return GameOfLifeSnapshot(
            active=self._active,
            version=self._version,
            generation=self._generation,
            is_running=self._is_running,
            is_edit_mode=self._is_edit_mode,
            active_pattern_name=self._active_pattern_name,
            active_pattern_rotation=self._active_pattern_rotation,
            grid=tuple(tuple(row) for row in self._game.grid),
            dirty_cells=tuple(dirty_cells),
            full_redraw=full_redraw,
        )

    def _clear_dirty_locked(self) -> None:
        self._dirty_cells.clear()
        self._full_redraw = False

    def _compute_active_pattern_cells_locked(self, anchor_row: int, anchor_col: int) -> list[tuple[int, int]]:
        if self._active_pattern_name is None:
            return []
        pattern = PATTERNS[self._active_pattern_name]
        offsets = rotate_offsets(pattern["cells"], self._active_pattern_rotation)  # type: ignore[arg-type]
        cells: list[tuple[int, int]] = []
        for row_offset, col_offset in offsets:
            row = anchor_row + row_offset
            col = anchor_col + col_offset
            if 0 <= row < self.rows and 0 <= col < self.cols:
                cells.append((row, col))
        return cells

    def _broadcast_snapshot(self, snapshot: GameOfLifeSnapshot | None) -> None:
        if snapshot is None:
            return
        with self._lock:
            surfaces = list(self._surfaces)
        removed = False
        for surface in surfaces:
            if not self._deliver_snapshot(surface, snapshot):
                removed = True
        if removed:
            self._sync_heartbeat()

    def _deliver_snapshot(self, surface: Any, snapshot: GameOfLifeSnapshot) -> bool:
        try:
            keep_surface = bool(surface.handle_runtime_snapshot(snapshot))
        except RuntimeError as exc:
            if is_deleted_parent_slot_error(exc):
                keep_surface = False
            else:
                raise
        except Exception:
            logger.exception("Failed to update Conway surface")
            keep_surface = False
        if keep_surface:
            return True
        with self._lock:
            self._surfaces = [item for item in self._surfaces if item is not surface]
        return False

    def _should_run_heartbeat_locked(self) -> bool:
        if not self._active or not self._is_running:
            return False
        return any(bool(getattr(surface, "is_stream_ready", lambda: False)()) for surface in self._surfaces)

    def _sync_heartbeat(self) -> None:
        with self._lock:
            should_run = self._should_run_heartbeat_locked()
            timer = self._heartbeat_timer
            if should_run and timer is None:
                if self._is_running:
                    self._last_tick_monotonic = time.monotonic()
                self._heartbeat_timer = ui.timer(self.update_interval, self._heartbeat_tick)
                return
            if should_run or timer is None:
                return
            self._heartbeat_timer = None
        try:
            timer.cancel()
        except Exception:
            logger.debug("Failed to cancel Conway heartbeat", exc_info=True)


def _resolve_client(client: Any | None = None) -> Any | None:
    if client is not None:
        return client
    try:
        return ui.context.client
    except Exception:
        return None


def _cleanup_runtime_from_client(client: Any) -> None:
    runtime = getattr(client, _RUNTIME_ATTR, None)
    if isinstance(runtime, GameOfLifeRuntime):
        runtime.shutdown()
    for attr_name in (_RUNTIME_ATTR, _RUNTIME_DISCONNECT_ATTR):
        try:
            if hasattr(client, attr_name):
                delattr(client, attr_name)
        except Exception:
            pass


def get_game_of_life_runtime(client: Any | None = None) -> GameOfLifeRuntime | None:
    resolved_client = _resolve_client(client)
    if resolved_client is None:
        return None
    runtime = getattr(resolved_client, _RUNTIME_ATTR, None)
    if isinstance(runtime, GameOfLifeRuntime):
        return runtime
    runtime = GameOfLifeRuntime(resolved_client)
    try:
        setattr(resolved_client, _RUNTIME_ATTR, runtime)
    except Exception:
        logger.debug("Failed to store Conway runtime on client", exc_info=True)
        return None

    disconnect_handler = getattr(resolved_client, _RUNTIME_DISCONNECT_ATTR, None)
    if not callable(disconnect_handler):
        def _cleanup_on_disconnect() -> None:
            _cleanup_runtime_from_client(resolved_client)

        register_client_disconnect_handler(
            resolved_client,
            _cleanup_on_disconnect,
            logger=logger,
            attr_name=_RUNTIME_DISCONNECT_ATTR,
        )
    return runtime


def reset_game_of_life_runtime_for_tests(client: Any | None = None) -> None:
    resolved_client = _resolve_client(client)
    if resolved_client is None:
        return
    _cleanup_runtime_from_client(resolved_client)


def is_conway_easter_egg_active(config: Any | None = None) -> bool:
    cfg = config or get_global_config()
    email_cfg = getattr(cfg, "email", None)
    active_groups = list(getattr(email_cfg, "active_groups", []) or [])
    return CONWAY_EASTER_EGG_GROUP in active_groups


def sync_game_of_life_activation_from_config(client: Any | None = None) -> bool:
    runtime = get_game_of_life_runtime(client=client)
    if runtime is None:
        return False
    return runtime.sync_activation(is_conway_easter_egg_active())


def compute_overlay_visibility(*, stream_ready: bool, trigger_active: bool) -> bool:
    return bool(stream_ready and trigger_active)


def _register_client_layout_listener(client: Any, event_type: str, handler: Callable[[Any], None]) -> str | None:
    layout = getattr(client, "layout", None)
    if layout is None:
        return None
    on = getattr(layout, "on", None)
    listeners = getattr(layout, "_event_listeners", None)
    if not callable(on) or not isinstance(listeners, dict):
        return None

    before = set(listeners.keys())
    on(event_type, handler)
    after = getattr(layout, "_event_listeners", None)
    if not isinstance(after, dict):
        return None
    created = [listener_id for listener_id in after.keys() if listener_id not in before]
    return created[-1] if created else None


def _remove_client_layout_listener(client: Any, listener_id: str | None) -> None:
    if client is None or not listener_id:
        return
    layout = getattr(client, "layout", None)
    listeners = getattr(layout, "_event_listeners", None)
    if layout is None or not isinstance(listeners, dict):
        return
    if listener_id not in listeners:
        return
    try:
        del listeners[listener_id]
    except Exception:
        logger.debug("Failed to remove Conway layout listener", exc_info=True)
        return
    try:
        layout.update()
    except Exception:
        logger.debug("Failed to update layout after removing Conway listener", exc_info=True)


def _install_conway_styles() -> None:
    ui.add_head_html(
        f"""
<style>
    .cvd-gol-layer {{
        display: grid;
        grid-template-columns: repeat({GRID_COLS}, minmax(0, 1fr));
        grid-template-rows: repeat({GRID_ROWS}, minmax(0, 1fr));
        background: transparent;
    }}
    .cvd-gol-cell {{
        user-select: none;
        -webkit-user-select: none;
        transition: background 0.05s linear, box-shadow 0.05s linear;
    }}
    .cvd-gol-layer--interactive .cvd-gol-cell {{
        cursor: pointer;
    }}
    .cvd-gol-layer--interactive.cvd-gol-layer--edit .cvd-gol-cell {{
        box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.05);
    }}
    .cvd-gol-cell-dead {{
        background: transparent;
        box-shadow: none;
    }}
    .cvd-gol-cell-alive {{
        background: rgba(34, 197, 94, 0.62);
        box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.12);
    }}
    .cvd-gol-cell-preview {{
        background: rgba(251, 191, 36, 0.68);
        box-shadow:
            inset 0 0 0 1px rgba(255, 255, 255, 0.18),
            0 0 0 1px rgba(251, 191, 36, 0.26);
    }}
    .cvd-gol-status {{
        box-shadow: none;
        backdrop-filter: blur(6px);
    }}
    .cvd-gol-toolbar {{
        box-shadow: none;
        backdrop-filter: blur(6px);
    }}
    body.body--light .cvd-gol-status {{
        color: #334155;
        background: rgba(248, 250, 252, 0.96);
        border: 1px solid rgba(148, 163, 184, 0.28);
    }}
    body.body--light .cvd-gol-toolbar {{
        background: rgba(255, 255, 255, 0.96);
        border: 1px solid rgba(148, 163, 184, 0.22);
    }}
    body.body--dark .cvd-gol-status {{
        color: #e2e8f0;
        background: rgba(15, 23, 42, 0.92);
        border: 1px solid rgba(148, 163, 184, 0.18);
    }}
    body.body--dark .cvd-gol-toolbar {{
        background: rgba(15, 23, 42, 0.90);
        border: 1px solid rgba(148, 163, 184, 0.18);
    }}
    .cvd-gol-help-text {{
        color: #475569;
    }}
    body.body--dark .cvd-gol-help-text {{
        color: #cbd5e1;
    }}
    .cvd-gol-toolbar-btn .q-btn {{
        border-radius: 10px !important;
        min-width: 40px !important;
        min-height: 40px !important;
        padding: 0 !important;
        box-shadow: none !important;
        transition:
            transform 0.12s ease,
            box-shadow 0.12s ease,
            filter 0.12s ease;
    }}
    .cvd-gol-toolbar-btn .q-icon {{
        font-size: 18px !important;
    }}
    body.body--light .cvd-gol-toolbar-btn .q-btn:hover {{
        transform: none;
        box-shadow: none !important;
        filter: brightness(0.98);
    }}
    body.body--dark .cvd-gol-toolbar-btn .q-btn:hover {{
        transform: none;
        box-shadow: none !important;
        filter: brightness(1.08);
    }}
</style>
"""
    )


def _create_toolbar_container(class_names: str) -> Any:
    card_factory = getattr(ui, "card", None)
    if callable(card_factory):
        return card_factory().classes(class_names).props("flat bordered")
    return ui.element("div").classes(class_names)


class _StreamAwareSurface:
    def __init__(self, *, stream_host_id: str) -> None:
        self.stream_host_id = str(stream_host_id)
        self.snapshot: GameOfLifeSnapshot | None = None
        self._stream_phase = ""
        self._closed = False
        self._client = _resolve_client()
        self._layout_listener_ids: list[str] = []
        self.runtime = get_game_of_life_runtime()
        self._register_layout_listener(STREAM_PHASE_EVENT, self._handle_stream_phase_event)
        if self.runtime is not None:
            self.runtime.register_surface(self)

    def is_stream_ready(self) -> bool:
        return self._stream_phase == STREAM_READY_PHASE

    def _register_layout_listener(self, event_type: str, handler: Callable[[Any], None]) -> None:
        listener_id = _register_client_layout_listener(self._client, event_type, handler)
        if listener_id is not None:
            self._layout_listener_ids.append(listener_id)

    def _remove_layout_listeners(self) -> None:
        for listener_id in self._layout_listener_ids:
            _remove_client_layout_listener(self._client, listener_id)
        self._layout_listener_ids.clear()

    def _handle_stream_phase_event(self, event: Any) -> None:
        if self._closed:
            return
        args = getattr(event, "args", {}) or {}
        host_id = str(args.get("host_id", "") or "")
        if host_id != self.stream_host_id:
            return
        phase = str(args.get("phase", "") or "")
        if phase == self._stream_phase:
            return
        self._stream_phase = phase
        if self.runtime is not None:
            self.runtime.handle_surface_stream_phase_change(self)

    def _mark_closed(self) -> None:
        self._closed = True
        self._remove_layout_listeners()
        if self.runtime is not None:
            self.runtime.unregister_surface(self)
            self.runtime = None


class DashboardOverlayBinder(_StreamAwareSurface):
    def __init__(self, *, stream_host_id: str, controls_host_id: str) -> None:
        self.controls_host_id = str(controls_host_id)
        self.overlay_host_id = f"{stream_host_id}-gol-layer"
        self.status_label: Any | None = None
        self.controls_host: Any | None = None
        self.pattern_toolbar: Any | None = None
        self.run_button: Any | None = None
        self.step_button: Any | None = None
        self.edit_button: Any | None = None
        self.pattern_buttons: dict[str, Any] = {}
        self.preview_cells: set[tuple[int, int]] = set()
        self.hover_anchor: tuple[int, int] | None = None
        self.is_drawing = False
        self.draw_value = 1
        self._last_visible: bool | None = None
        self._last_rendered_version = -1
        self.overlay_root, self.cells = self._create_overlay_cells()
        super().__init__(stream_host_id=stream_host_id)
        self.overlay_root.on("mouseleave", lambda _event: self.stop_drawing())
        self._register_layout_listener("mouseup", lambda _event: self.stop_drawing())
        self._wire_cell_handlers()
        self._set_visible(False)

    def _create_overlay_cells(self) -> tuple[Any, list[list[Any]]]:
        overlay_root = (
            ui.element("div")
            .classes("cvd-gol-layer cvd-gol-layer--interactive absolute inset-0 z-10 overflow-hidden")
            .props(f'id={self.overlay_host_id} data-conway-ready=false')
        )
        cells: list[list[Any]] = []
        with overlay_root:
            for row in range(GRID_ROWS):
                row_cells: list[Any] = []
                for col in range(GRID_COLS):
                    cell = ui.element("div").classes("cvd-gol-cell cvd-gol-cell-dead")
                    row_cells.append(cell)
                cells.append(row_cells)
        return overlay_root, cells

    def _wire_cell_handlers(self) -> None:
        for row in range(GRID_ROWS):
            for col in range(GRID_COLS):
                cell = self.cells[row][col]
                cell.on("mousedown", lambda _event, row=row, col=col: self.cell_mouse_down(row, col))
                cell.on("mouseenter", lambda _event, row=row, col=col: self.cell_mouse_enter(row, col))

    def build_controls(self) -> None:
        self.controls_host = ui.column().classes("w-full gap-2").props(
            f'id={self.controls_host_id} data-conway-ready=false'
        )
        self.controls_host.visible = False
        with self.controls_host:
            self.status_label = (
                ui.label("")
                .classes("cvd-gol-status px-3 py-2 rounded-lg text-caption font-medium self-start")
            )

            with _create_toolbar_container("cvd-gol-toolbar w-full p-3"):
                with ui.row().classes("items-center justify-start gap-2 flex-wrap"):
                    self.run_button = ui.button(
                        on_click=self.toggle_running,
                        icon="play_arrow",
                    ).classes("cvd-gol-toolbar-btn").props("dense unelevated no-caps color=positive")
                    self.run_button.tooltip("Start / Stop")

                    self.step_button = ui.button(
                        on_click=self.single_step,
                        icon="skip_next",
                    ).classes("cvd-gol-toolbar-btn").props("dense unelevated no-caps color=primary")
                    self.step_button.tooltip("Step forward")

                    self.edit_button = ui.button(
                        on_click=self.toggle_edit_mode,
                        icon="edit",
                    ).classes("cvd-gol-toolbar-btn").props("dense unelevated no-caps color=primary")
                    self.edit_button.tooltip("Edit mode")

                    reset_button = ui.button(
                        on_click=self.randomize_board,
                        icon="refresh",
                    ).classes("cvd-gol-toolbar-btn").props("dense unelevated no-caps color=secondary")
                    reset_button.tooltip("Randomize board")

                    clear_button = ui.button(
                        on_click=self.clear_board,
                        icon="delete_sweep",
                    ).classes("cvd-gol-toolbar-btn").props("dense unelevated no-caps color=negative")
                    clear_button.tooltip("Clear board")

            self.pattern_toolbar = _create_toolbar_container("cvd-gol-toolbar w-full p-3 hidden")
            with self.pattern_toolbar:
                with ui.row().classes("items-center justify-start gap-2 flex-wrap"):
                    for pattern_name, meta in PATTERNS.items():
                        button = ui.button(
                            on_click=lambda name=pattern_name: self.select_pattern(name),
                            icon=meta["icon"],  # type: ignore[arg-type]
                        ).classes("cvd-gol-toolbar-btn").props("dense unelevated no-caps color=primary")
                        button.tooltip(meta["tooltip"])  # type: ignore[arg-type]
                        self.pattern_buttons[pattern_name] = button

            ui.label(
                "Edit mode allows free drawing. Selecting an object shows a placement preview, and"
                " clicking the same icon again rotates it by 90 degrees."
            ).classes("cvd-gol-help-text text-caption max-w-[820px] leading-relaxed")

    def handle_runtime_snapshot(self, snapshot: GameOfLifeSnapshot) -> bool:
        if self._closed:
            return False
        try:
            self.snapshot = snapshot
            visible = compute_overlay_visibility(
                stream_ready=self.is_stream_ready(),
                trigger_active=snapshot.active,
            )
            became_visible = visible and not self._last_visible
            self._set_visible(visible)
            if not visible:
                self.preview_cells.clear()
                self.hover_anchor = None
                self.is_drawing = False
                self._last_rendered_version = -1
                return True

            if became_visible or snapshot.full_redraw or self._last_rendered_version < 0:
                self._redraw_all_cells(self.preview_cells)
            elif snapshot.version != self._last_rendered_version:
                dirty_cells = set(snapshot.dirty_cells) | self.preview_cells
                for row, col in dirty_cells:
                    self._update_cell_visual(row, col, self.preview_cells)
            self._last_rendered_version = snapshot.version
            self._update_status(snapshot)
            return True
        except RuntimeError as exc:
            if is_deleted_parent_slot_error(exc):
                self._mark_closed()
                return False
            raise

    def _set_visible(self, visible: bool) -> None:
        if self._last_visible is visible:
            return
        self.overlay_root.visible = visible
        self.overlay_root.update()
        if self.controls_host is not None:
            self.controls_host.visible = visible
            self.controls_host.update()
        self._last_visible = visible

    def _redraw_all_cells(self, preview_cells: set[tuple[int, int]] | None = None) -> None:
        if self.snapshot is None:
            return
        preview_cells = preview_cells or set()
        for row in range(GRID_ROWS):
            for col in range(GRID_COLS):
                self._update_cell_visual(row, col, preview_cells)

    def _update_cell_visual(self, row: int, col: int, preview_cells: set[tuple[int, int]] | None = None) -> None:
        if self.snapshot is None:
            return
        preview_cells = preview_cells or set()
        cell = self.cells[row][col]
        is_alive = self.snapshot.grid[row][col] == 1
        is_preview = (row, col) in preview_cells
        cell.classes(remove="cvd-gol-cell-alive cvd-gol-cell-dead cvd-gol-cell-preview")
        if is_preview:
            cell.classes(add="cvd-gol-cell-preview")
        elif is_alive:
            cell.classes(add="cvd-gol-cell-alive")
        else:
            cell.classes(add="cvd-gol-cell-dead")

    def _update_status(self, snapshot: GameOfLifeSnapshot) -> None:
        if self.status_label is None:
            return

        status_text = (
            f"Status: {'Running' if snapshot.is_running else 'Stopped'}"
            f" | Mode: {'Edit' if snapshot.is_edit_mode else 'Simulation'}"
            f" | Generation: {snapshot.generation}"
        )
        if snapshot.active_pattern_name is not None and snapshot.is_edit_mode:
            tooltip = str(PATTERNS[snapshot.active_pattern_name]["tooltip"])
            status_text += f" | Pattern: {tooltip} ({snapshot.active_pattern_rotation} deg)"
        self.status_label.text = status_text
        self.status_label.update()

        if self.run_button is None or self.step_button is None or self.edit_button is None or self.pattern_toolbar is None:
            return

        if snapshot.is_running:
            self.run_button.props("icon=stop color=negative")
            self.step_button.disable()
            self.edit_button.disable()
        else:
            self.run_button.props("icon=play_arrow color=positive")
            self.step_button.enable()
            self.edit_button.enable()

        if snapshot.is_edit_mode:
            self.edit_button.props("icon=edit_off color=orange")
            self.overlay_root.classes(add="cvd-gol-layer--edit")
            self.pattern_toolbar.classes(remove="hidden")
        else:
            self.edit_button.props("icon=edit color=primary")
            self.overlay_root.classes(remove="cvd-gol-layer--edit")
            self.pattern_toolbar.classes(add="hidden")

        self.run_button.update()
        self.step_button.update()
        self.edit_button.update()
        self.pattern_toolbar.update()

        for pattern_name, button in self.pattern_buttons.items():
            if pattern_name == snapshot.active_pattern_name:
                button.props("color=orange")
            else:
                button.props("color=primary")
            button.update()

    def stop_drawing(self) -> None:
        self.is_drawing = False

    def _current_snapshot(self) -> GameOfLifeSnapshot | None:
        if self.runtime is None:
            return self.snapshot
        return self.runtime.snapshot()

    def clear_preview(self) -> None:
        if not self.preview_cells:
            return
        previous_preview = list(self.preview_cells)
        self.preview_cells.clear()
        for row, col in previous_preview:
            self._update_cell_visual(row, col)

    def set_preview(self, anchor_row: int, anchor_col: int) -> None:
        snapshot = self._current_snapshot()
        self.hover_anchor = (anchor_row, anchor_col)
        self.clear_preview()
        if (
            snapshot is None
            or not snapshot.active
            or not snapshot.is_edit_mode
            or snapshot.is_running
            or snapshot.active_pattern_name is None
            or self.runtime is None
        ):
            return
        self.preview_cells = set(self.runtime.compute_active_pattern_cells(anchor_row, anchor_col))
        for row, col in self.preview_cells:
            self._update_cell_visual(row, col, self.preview_cells)

    def cell_mouse_down(self, row: int, col: int) -> None:
        snapshot = self._current_snapshot()
        if snapshot is None or self.runtime is None:
            return
        if not snapshot.active or not snapshot.is_edit_mode or snapshot.is_running:
            return

        if snapshot.active_pattern_name is not None:
            self.runtime.place_active_pattern(row, col)
            self.set_preview(row, col)
            return

        self.is_drawing = True
        self.draw_value = 1 if snapshot.grid[row][col] == 0 else 0
        self.runtime.apply_draw(row, col, self.draw_value)

    def cell_mouse_enter(self, row: int, col: int) -> None:
        snapshot = self._current_snapshot()
        if snapshot is None or self.runtime is None:
            return
        if not snapshot.active or not snapshot.is_edit_mode or snapshot.is_running:
            return

        if snapshot.active_pattern_name is not None:
            self.set_preview(row, col)
            return

        if not self.is_drawing:
            return
        self.runtime.apply_draw(row, col, self.draw_value)

    def toggle_running(self) -> None:
        if self.runtime is None:
            return
        self.stop_drawing()
        self.clear_preview()
        self.runtime.toggle_running()

    def single_step(self) -> None:
        if self.runtime is None:
            return
        self.runtime.single_step()

    def toggle_edit_mode(self) -> None:
        if self.runtime is None:
            return
        self.stop_drawing()
        self.clear_preview()
        self.runtime.toggle_edit_mode()

    def select_pattern(self, pattern_name: str) -> None:
        if self.runtime is None:
            return
        self.runtime.select_pattern(pattern_name)
        if self.hover_anchor is not None:
            self.set_preview(self.hover_anchor[0], self.hover_anchor[1])
        else:
            self.clear_preview()

    def randomize_board(self) -> None:
        if self.runtime is None:
            return
        self.stop_drawing()
        self.clear_preview()
        self.runtime.randomize_board()

    def clear_board(self) -> None:
        if self.runtime is None:
            return
        self.stop_drawing()
        self.clear_preview()
        self.runtime.clear_board()


class PassiveOverlayController(_StreamAwareSurface):
    def __init__(self, *, stream_host_id: str, on_change: Callable[[], None]) -> None:
        self._on_change = on_change
        self._svg_cache_key: tuple[int, int, int, bool] | None = None
        self._svg_cache: list[str] = []
        super().__init__(stream_host_id=stream_host_id)

    def handle_runtime_snapshot(self, snapshot: GameOfLifeSnapshot) -> bool:
        if self._closed:
            return False
        self.snapshot = snapshot
        self._svg_cache_key = None
        try:
            self._on_change()
            return True
        except RuntimeError as exc:
            if is_deleted_parent_slot_error(exc):
                self._mark_closed()
                return False
            raise

    def render_svg_fragments(self, preview_width: int, preview_height: int) -> list[str]:
        snapshot = self.snapshot
        visible = bool(
            snapshot is not None
            and compute_overlay_visibility(
                stream_ready=self.is_stream_ready(),
                trigger_active=snapshot.active,
            )
        )
        cache_key = (
            snapshot.version if snapshot is not None else -1,
            int(preview_width),
            int(preview_height),
            visible,
        )
        if self._svg_cache_key == cache_key:
            return list(self._svg_cache)
        self._svg_cache_key = cache_key
        if not visible or snapshot is None:
            self._svg_cache = []
            return []

        cell_width = max(1.0, float(preview_width) / float(GRID_COLS))
        cell_height = max(1.0, float(preview_height) / float(GRID_ROWS))
        fragments: list[str] = ['<g pointer-events="none" aria-hidden="true">']
        for row in range(GRID_ROWS):
            for col in range(GRID_COLS):
                if snapshot.grid[row][col] != 1:
                    continue
                x = col * cell_width
                y = row * cell_height
                fragments.append(
                    '<rect '
                    f'x="{x:.3f}" y="{y:.3f}" width="{cell_width:.3f}" height="{cell_height:.3f}" '
                    'fill="rgba(34, 197, 94, 0.42)" '
                    'stroke="rgba(255,255,255,0.12)" '
                    'stroke-width="0.5" '
                    'vector-effect="non-scaling-stroke" />'
                )
        fragments.append("</g>")
        self._svg_cache = fragments
        return list(fragments)


def create_dashboard_game_layer(*, stream_host_id: str, controls_host_id: str) -> DashboardOverlayBinder:
    _install_conway_styles()
    return DashboardOverlayBinder(stream_host_id=stream_host_id, controls_host_id=controls_host_id)


def create_passive_game_layer(*, stream_host_id: str, on_change: Callable[[], None]) -> PassiveOverlayController:
    return PassiveOverlayController(stream_host_id=stream_host_id, on_change=on_change)
