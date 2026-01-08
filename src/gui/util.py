from __future__ import annotations
from typing import Optional, Any
from nicegui import ui, background_tasks, core, app, Client

# --- Tab helpers: dynamic title & favicon per client/all clients ---
def set_tab(title: str | None = None, icon_url: str | None = None, client: Optional[Client] = None) -> None:
    """Set browser tab title and/or favicon on the given or current client.

    Safe no-op if no client context is available.
    """
    # If a client is provided, use it; otherwise, run in the current client context
    c = client
    js_parts: list[str] = []
    if title is not None:
        js_parts.append(f'document.title = {title!r};')
    if icon_url is not None:
        js_parts.append(
            (
                "const head = document.head || document.getElementsByTagName('head')[0];\n"
                "for (const rel of ['icon','shortcut icon','apple-touch-icon']) {\n"
                "  let link = document.querySelector(`link[rel=\"${rel}\"]`);\n"
                "  if (!link) { link = document.createElement('link'); link.rel = rel; head.appendChild(link); }\n"
                f"  link.href = {icon_url!r};\n"
                "}\n"
            )
        )
    if js_parts:
        code = '\n'.join(js_parts)
        try:
            if c is not None and hasattr(c, 'run_javascript'):
                c.run_javascript(code)
            else:
                # Executes on the current client's context (if any)
                ui.run_javascript(code)
        except Exception:
            pass


def set_tab_all(title: str | None = None, icon_url: str | None = None) -> None:
    """Broadcast title and/or favicon update to all connected clients."""
    clients_dict = getattr(app, 'clients', {})
    try:
        clients = list(getattr(clients_dict, 'values', lambda: [])())
    except Exception:
        clients = []
    for c in clients:
        set_tab(title, icon_url, client=c)


def set_favicon_default_all() -> None:
    """Restore the default favicon for all clients if configured in app.storage.general."""
    try:
        default_icon = app.storage.general.get('cvd.default_favicon')
        if default_icon:
            set_tab_all(icon_url=str(default_icon))
    except Exception:
        pass


def _svg_data_url(svg: str) -> str:
    # Minimal escaping; UTF-8 inline SVG works well for favicons
    return 'data:image/svg+xml;utf8,' + svg


def favicon_check_circle_green() -> str:
    """Green check_circle style SVG data URL."""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 24 24'>"
        "<circle cx='12' cy='12' r='10' fill='#22c55e'/>"
        "<path d='M9 12.5l2 2 4-4' stroke='white' stroke-width='2' fill='none' stroke-linecap='round' stroke-linejoin='round'/>"
        "</svg>"
    )
    return _svg_data_url(svg)


def favicon_highlight_off_red() -> str:
    """Red highlight_off style SVG data URL."""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 24 24'>"
        "<circle cx='12' cy='12' r='10' fill='#ef4444'/>"
        "<path d='M8 8l8 8M16 8l-8 8' stroke='white' stroke-width='2' stroke-linecap='round'/>"
        "</svg>"
    )
    return _svg_data_url(svg)


def schedule_bg(coroutine: Any, name: Optional[str] = None) -> Optional[Any]:
    """Safely schedule a coroutine on NiceGUI's event loop.

    If the NiceGUI loop is not yet available (core.loop is None), defer scheduling
    using ui.timer so it runs once the loop is ready. Returns the created task or None
    when scheduling is deferred.
    """
    task_name = name or 'bg_task'
    try:
        if core.loop is None:
            ui.timer(0.0, lambda: background_tasks.create_lazy(coroutine, name=task_name), once=True)
            return None
        background_tasks.create_lazy(coroutine, name=task_name)
        return None
    except AssertionError:
        # In case create/create_lazy asserts due to loop not being ready, defer via timer
        ui.timer(0.0, lambda: background_tasks.create_lazy(coroutine, name=task_name), once=True)
        return None


def cancel_task_safely(task: Any) -> None:
    """Attempt to cancel an asyncio.Task or NiceGUI TaskProxy without raising.

    Accepts None and objects without done()/cancel() gracefully.
    """
    if not task:
        return
    try:
        if hasattr(task, 'done'):
            if not task.done():
                task.cancel()
        else:
            task.cancel()
    except Exception:
        # Best-effort; ignore any cancellation errors
        pass

def safe_ui_operation(
    operation: Any,
    error_msg: str = "Operation failed",
    success_msg: Optional[str] = None,
    logger: Optional[Any] = None
) -> None:
    """
    Executes a UI operation safely, handling exceptions and notifying the user.
    
    Args:
        operation: Callable to execute.
        error_msg: Message to show/log on error.
        success_msg: Optional message to show on success.
        logger: Optional logger to record errors.
    """
    try:
        operation()
        if success_msg:
            ui.notify(success_msg, type='positive')
    except Exception as e:
        if logger:
            logger.error(f"{error_msg}: {e}")
        ui.notify(f"{error_msg}: {e}", type='negative')
