from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence

from nicegui import app, ui

from src.config import get_logger
from src.gui import cleanup
from src.update import restart_self

logger = get_logger("gui.power_actions")
_STATUS_PAGE_DELAY_SECONDS = 1.0


@dataclass(frozen=True)
class PowerActionSpec:
    key: str
    label: str
    description: str
    icon: str
    route: str
    confirmation_title: str
    confirmation_message: str
    confirm_label: str
    status_icon: str
    status_icon_classes: str
    status_title: str
    status_message: str


_POWER_ACTIONS: tuple[PowerActionSpec, ...] = (
    PowerActionSpec(
        key="app_shutdown",
        label="Shut Down Application",
        description="Stops only the running application and web server.",
        icon="power_settings_new",
        route="/shutdown",
        confirmation_title="Shut down application?",
        confirmation_message="Do you really want to shut down the application?",
        confirm_label="Yes, shut down application",
        status_icon="power_settings_new",
        status_icon_classes="text-6xl text-negative",
        status_title="Shutting down application",
        status_message="You can close this window now.",
    ),
    PowerActionSpec(
        key="app_restart",
        label="Restart Application",
        description="Restarts only the application; the Raspberry Pi stays on.",
        icon="restart_alt",
        route="/restart",
        confirmation_title="Restart application?",
        confirmation_message="Do you really want to restart the application?",
        confirm_label="Yes, restart application",
        status_icon="restart_alt",
        status_icon_classes="text-6xl text-warning",
        status_title="Restarting application",
        status_message="Please wait a moment; the interface will reconnect afterward.",
    ),
    PowerActionSpec(
        key="pi_restart",
        label="Restart Raspberry Pi",
        description="Restarts the Raspberry Pi and the application.",
        icon="restart_alt",
        route="/pi-restart",
        confirmation_title="Restart Raspberry Pi?",
        confirmation_message="Do you really want to restart the Raspberry Pi?",
        confirm_label="Yes, restart Raspberry Pi",
        status_icon="restart_alt",
        status_icon_classes="text-6xl text-warning",
        status_title="Restarting Raspberry Pi",
        status_message="The connection will be interrupted briefly and then restored automatically.",
    ),
    PowerActionSpec(
        key="pi_shutdown",
        label="Shut Down Raspberry Pi",
        description="Shuts down the Raspberry Pi completely.",
        icon="power_settings_new",
        route="/pi-shutdown",
        confirmation_title="Shut down Raspberry Pi?",
        confirmation_message="Do you really want to shut down the Raspberry Pi?",
        confirm_label="Yes, shut down Raspberry Pi",
        status_icon="power_settings_new",
        status_icon_classes="text-6xl text-negative",
        status_title="Shutting down Raspberry Pi",
        status_message="After shutdown completes, the device can be powered off safely.",
    ),
)
_POWER_ACTIONS_BY_KEY = {spec.key: spec for spec in _POWER_ACTIONS}
_SYSTEM_POWER_ACTIONS = {"pi_restart", "pi_shutdown"}


def list_power_actions() -> tuple[PowerActionSpec, ...]:
    """Return the power actions in menu order."""
    return _POWER_ACTIONS


def get_power_action_spec(action_key: str) -> PowerActionSpec:
    """Return the configured UI and execution metadata for a power action."""
    try:
        return _POWER_ACTIONS_BY_KEY[action_key]
    except KeyError as exc:
        raise ValueError(f"Unknown power action: {action_key}") from exc


def _add_command_candidate(
    candidates: list[list[str]],
    seen: set[tuple[str, ...]],
    *parts: str | None,
) -> None:
    command = [str(part) for part in parts if part]
    if not command:
        return
    marker = tuple(command)
    if marker in seen:
        return
    seen.add(marker)
    candidates.append(command)


def get_system_power_command_candidates(
    action_key: str,
    *,
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    geteuid: Callable[[], int] | None = None,
) -> tuple[list[str], ...]:
    """Return candidate commands for Raspberry Pi power operations on the active platform."""
    if action_key not in _SYSTEM_POWER_ACTIONS:
        return ()

    resolved_platform = str(platform or sys.platform).lower()
    if resolved_platform == "win32":
        return (
            ["shutdown", "/r", "/t", "0"]
            if action_key == "pi_restart"
            else ["shutdown", "/s", "/t", "0"],
        )

    if not resolved_platform.startswith("linux"):
        return ()

    effective_geteuid = geteuid or getattr(os, "geteuid", None)
    is_root = False
    if effective_geteuid is not None:
        try:
            is_root = effective_geteuid() == 0
        except Exception:
            is_root = False

    sudo = which("sudo")
    systemctl = which("systemctl")
    shutdown = which("shutdown")
    direct_binary = which("reboot" if action_key == "pi_restart" else "poweroff")
    systemctl_action = "reboot" if action_key == "pi_restart" else "poweroff"
    shutdown_mode = "-r" if action_key == "pi_restart" else "-h"

    candidates: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    if is_root:
        _add_command_candidate(candidates, seen, systemctl, systemctl_action)
        _add_command_candidate(candidates, seen, direct_binary)
        _add_command_candidate(candidates, seen, shutdown, shutdown_mode, "now")

    _add_command_candidate(candidates, seen, sudo, "-n", systemctl, systemctl_action)
    _add_command_candidate(candidates, seen, sudo, "-n", direct_binary)
    _add_command_candidate(candidates, seen, sudo, "-n", shutdown, shutdown_mode, "now")
    _add_command_candidate(candidates, seen, systemctl, systemctl_action)
    _add_command_candidate(candidates, seen, direct_binary)
    _add_command_candidate(candidates, seen, shutdown, shutdown_mode, "now")

    return tuple(candidates)


def _spawn_detached_command(command: Sequence[str]) -> None:
    """Start a detached command without inheriting the current process streams."""
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
        return

    subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def execute_system_power_action(
    action_key: str,
    *,
    command_candidates: Sequence[Sequence[str]] | None = None,
    runner: Callable[[Sequence[str]], None] = _spawn_detached_command,
) -> list[str]:
    """Run the first working detached system power command for the given action."""
    candidates = [list(command) for command in (command_candidates or get_system_power_command_candidates(action_key))]
    if not candidates:
        raise RuntimeError("No suitable system power action was found for this platform or user permissions.")

    failures: list[str] = []
    for command in candidates:
        try:
            runner(command)
            logger.info("Triggered %s via %s", action_key, command)
            return command
        except Exception as exc:
            failures.append(f"{command}: {exc}")
            logger.warning("Failed to trigger %s via %s", action_key, command, exc_info=True)

    raise RuntimeError("System power action could not be started: " + "; ".join(failures))


async def trigger_power_action(
    action_key: str,
    *,
    navigate: Callable[[str], None] | None = None,
    sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
    cleanup_func: Callable[[], None] = cleanup.cleanup_application,
    app_shutdown_func: Callable[[], None] = app.shutdown,
    restart_func: Callable[[], None] = restart_self,
    system_action_func: Callable[[str], list[str]] = execute_system_power_action,
) -> None:
    """Navigate to the status page and execute the selected power action."""
    spec = get_power_action_spec(action_key)

    if action_key in _SYSTEM_POWER_ACTIONS and not get_system_power_command_candidates(action_key):
        raise RuntimeError("No suitable system command was found for this action.")

    if navigate is None:
        def _default_navigate(route: str) -> None:
            ui.navigate.to(route, new_tab=False)

        navigate_func = _default_navigate
    else:
        navigate_func = navigate

    navigate_func(spec.route)
    await sleep_func(_STATUS_PAGE_DELAY_SECONDS)
    cleanup_func()
    await sleep_func(0.2)

    if action_key == "app_shutdown":
        app_shutdown_func()
        return

    if action_key == "app_restart":
        await asyncio.to_thread(restart_func)
        return

    await asyncio.to_thread(system_action_func, action_key)
    await sleep_func(0.2)
    app_shutdown_func()
