import asyncio

from src.gui import power_actions


def test_list_power_actions_exposes_expected_menu_order() -> None:
    assert [spec.key for spec in power_actions.list_power_actions()] == [
        "app_shutdown",
        "app_restart",
        "pi_restart",
        "pi_shutdown",
    ]


def test_get_system_power_command_candidates_linux_uses_sudo_when_not_root() -> None:
    command_map = {
        "sudo": "/usr/bin/sudo",
        "systemctl": "/usr/bin/systemctl",
        "shutdown": "/usr/sbin/shutdown",
        "reboot": "/usr/sbin/reboot",
        "poweroff": "/usr/sbin/poweroff",
    }

    candidates = power_actions.get_system_power_command_candidates(
        "pi_restart",
        platform="linux",
        which=command_map.get,
        geteuid=lambda: 1000,
    )

    assert candidates == (
        ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "reboot"],
        ["/usr/bin/sudo", "-n", "/usr/sbin/reboot"],
        ["/usr/bin/sudo", "-n", "/usr/sbin/shutdown", "-r", "now"],
        ["/usr/bin/systemctl", "reboot"],
        ["/usr/sbin/reboot"],
        ["/usr/sbin/shutdown", "-r", "now"],
    )


def test_execute_system_power_action_tries_fallback_candidate() -> None:
    calls: list[list[str]] = []

    def runner(command) -> None:
        cmd = list(command)
        calls.append(cmd)
        if len(calls) == 1:
            raise OSError("first command failed")

    executed = power_actions.execute_system_power_action(
        "pi_shutdown",
        command_candidates=(["first"], ["second"]),
        runner=runner,
    )

    assert executed == ["second"]
    assert calls == [["first"], ["second"]]


def test_trigger_power_action_app_restart_navigates_and_restarts() -> None:
    calls: list[object] = []

    async def fake_sleep(_: float) -> None:
        calls.append("sleep")

    def navigate(route: str) -> None:
        calls.append(("navigate", route))

    def cleanup() -> None:
        calls.append("cleanup")

    def restart() -> None:
        calls.append("restart")

    asyncio.run(
        power_actions.trigger_power_action(
            "app_restart",
            navigate=navigate,
            sleep_func=fake_sleep,
            cleanup_func=cleanup,
            app_shutdown_func=lambda: calls.append("shutdown"),
            restart_func=restart,
        )
    )

    assert calls == [
        ("navigate", "/restart"),
        "sleep",
        "cleanup",
        "sleep",
        "restart",
    ]


def test_trigger_power_action_pi_shutdown_runs_system_action_and_app_shutdown(monkeypatch) -> None:
    calls: list[object] = []

    async def fake_sleep(_: float) -> None:
        calls.append("sleep")

    def cleanup() -> None:
        calls.append("cleanup")

    def system_action(action_key: str) -> list[str]:
        calls.append(("system_action", action_key))
        return ["sudo", "poweroff"]

    monkeypatch.setattr(
        power_actions,
        "get_system_power_command_candidates",
        lambda action_key, **_: (["mock", action_key],),
    )

    asyncio.run(
        power_actions.trigger_power_action(
            "pi_shutdown",
            navigate=lambda route: calls.append(("navigate", route)),
            sleep_func=fake_sleep,
            cleanup_func=cleanup,
            app_shutdown_func=lambda: calls.append("shutdown"),
            system_action_func=system_action,
        )
    )

    assert calls == [
        ("navigate", "/pi-shutdown"),
        "sleep",
        "cleanup",
        "sleep",
        ("system_action", "pi_shutdown"),
        "sleep",
        "shutdown",
    ]
