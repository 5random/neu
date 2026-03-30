import logging
import argparse
import asyncio
import os
import secrets
import sys
from contextlib import nullcontext
from pathlib import Path

if sys.platform == "win32":
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is not None:
        asyncio.set_event_loop_policy(selector_policy())

# Projekt-Root zum Python-Pfad hinzufügen
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

#from nicegui_toolkit import inject_layout_tool
#inject_layout_tool()

from nicegui import ui, app
from nicegui.elements.timer import Timer as NiceGUITimer
from nicegui.timer import Timer as NiceGUIBaseTimer
from fastapi import Request
from fastapi.responses import JSONResponse
from src.config import load_config, get_logger
from src.gui.gui_ import create_gui
from src.gui.layout import compute_gui_title

from typing import Any, Callable, Dict

AsyncioExceptionHandler = Callable[[Any, Dict[str, Any]], object]
_nicegui_timer_patch_installed = False


def _is_benign_windows_connection_reset(context: Dict[str, Any]) -> bool:
    """Detect the noisy Windows transport shutdown error on client disconnect."""
    exc = context.get("exception")
    if not isinstance(exc, ConnectionResetError):
        return False
    if getattr(exc, "winerror", None) != 10054:
        return False

    handle_repr = repr(context.get("handle", ""))
    message = str(context.get("message", ""))
    return "_ProactorBasePipeTransport._call_connection_lost" in handle_repr or (
        "connection lost" in message.lower()
    )


def handle_asyncio_connection_lost(
    loop: Any,
    context: Dict[str, Any],
    previous_handler: AsyncioExceptionHandler | None = None,
) -> None:
    """Ignore the known Windows transport shutdown reset, forward everything else."""
    if _is_benign_windows_connection_reset(context):
        return
    if previous_handler is not None:
        previous_handler(loop, context)
        return
    loop.default_exception_handler(context)


def install_asyncio_exception_handler(logger: logging.Logger) -> None:
    """Install a loop exception handler for the known Windows disconnect noise."""
    if sys.platform != "win32":
        return

    def configure_loop_exception_handler() -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("No running asyncio loop available during startup")
            return

        previous_handler = loop.get_exception_handler()

        def chained_handler(active_loop: Any, context: Dict[str, Any]) -> None:
            handle_asyncio_connection_lost(active_loop, context, previous_handler)

        loop.set_exception_handler(chained_handler)
        logger.info("Installed Windows asyncio exception handler")

    app.on_startup(configure_loop_exception_handler)


def _is_deleted_parent_slot_error(exc: BaseException) -> bool:
    """Return True for the known NiceGUI timer error after parent deletion."""
    return isinstance(exc, RuntimeError) and "parent slot of the element has been deleted" in str(exc).lower()


def install_nicegui_timer_patch(logger: logging.Logger) -> None:
    """Patch NiceGUI timers to stop cleanly when their parent slot is already gone."""
    global _nicegui_timer_patch_installed

    if _nicegui_timer_patch_installed:
        return

    original_get_context = NiceGUITimer._get_context
    original_should_stop = NiceGUITimer._should_stop

    def patched_get_context(self: Any) -> Any:
        try:
            return original_get_context(self)
        except RuntimeError as exc:
            if not _is_deleted_parent_slot_error(exc):
                raise
            try:
                self.cancel()
            except Exception:
                pass
            logger.debug("Cancelled NiceGUI timer after parent slot deletion")
            return nullcontext()

    def patched_should_stop(self: Any) -> bool:
        if original_should_stop(self):
            return True
        try:
            _ = self.parent_slot
        except RuntimeError as exc:
            if not _is_deleted_parent_slot_error(exc):
                raise
            try:
                self.cancel()
            except Exception:
                pass
            return True
        return False

    def patched_cleanup(self: Any) -> None:
        NiceGUIBaseTimer._cleanup(self)
        if getattr(self, "_deleted", False):
            return
        try:
            parent_slot = self.parent_slot
        except RuntimeError as exc:
            if _is_deleted_parent_slot_error(exc):
                return
            raise
        if parent_slot is None:
            return
        try:
            parent_slot.parent.remove(self)
        except RuntimeError as exc:
            if not _is_deleted_parent_slot_error(exc):
                raise

    NiceGUITimer._get_context = patched_get_context
    NiceGUITimer._should_stop = patched_should_stop
    NiceGUITimer._cleanup = patched_cleanup
    _nicegui_timer_patch_installed = True
    logger.info("Installed NiceGUI timer parent-slot cleanup patch")


def is_headless_linux() -> bool:
    """Return True when running on Linux without an active graphical session."""
    if not sys.platform.startswith("linux"):
        return False
    return not any(os.environ.get(var) for var in ("DISPLAY", "WAYLAND_DISPLAY", "MIR_SOCKET"))


def parse_optional_bool(value: str | None) -> bool | None:
    """Parse a boolean-like environment variable value."""
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def resolve_config_bool(
    value: Any,
    logger: logging.Logger,
    *,
    field_name: str,
    default: bool = False,
) -> bool:
    """Resolve a bool-like config value without treating non-empty strings as True."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        parsed = parse_optional_bool(value)
        if parsed is not None:
            return parsed
    if value is None:
        return default
    logger.warning("Invalid %s value %r, falling back to %s", field_name, value, default)
    return default


def resolve_config_string(
    value: Any,
    logger: logging.Logger,
    *,
    field_name: str,
    default: str,
    allow_empty: bool = False,
) -> str:
    """Resolve a string config value without coercing non-string scalars."""
    if isinstance(value, bool) or isinstance(value, (list, dict)):
        logger.warning("Invalid %s value %r, falling back to %r", field_name, value, default)
        return default
    if value is None:
        return default
    if not isinstance(value, str):
        logger.warning("Invalid %s value %r, falling back to %r", field_name, value, default)
        return default
    normalized = value.strip()
    if not allow_empty and not normalized:
        logger.warning("Invalid %s value %r, falling back to %r", field_name, value, default)
        return default
    return normalized


def resolve_port(value: Any, logger: logging.Logger, default: int = 8080) -> int:
    """Convert a port value to int and fall back safely on invalid input."""
    try:
        port = int(value)
    except (TypeError, ValueError):
        logger.warning("Invalid port value %r, falling back to %s", value, default)
        return default
    if 1 <= port <= 65535:
        return port
    logger.warning("Port %s out of range, falling back to %s", port, default)
    return default


def normalize_root_path(value: Any) -> str:
    """Normalize an optional ASGI root path for reverse-proxy deployments."""
    if isinstance(value, bool) or isinstance(value, (list, dict)):
        raise ValueError("root_path must be a string")
    text = str(value or "").strip()
    if not text or text == "/":
        return ""
    if not text.startswith("/"):
        raise ValueError("root_path must start with '/' or be empty")
    return text.rstrip("/")


def resolve_root_path(value: Any, logger: logging.Logger, *, default: str = "") -> str:
    """Resolve a root_path config value using the same semantics as config validation."""
    try:
        return normalize_root_path(value)
    except ValueError as exc:
        logger.warning("Invalid gui.root_path value %r, falling back to %r (%s)", value, default, exc)
        return default


def resolve_ui_run_settings(
    cfg: Any,
    args: argparse.Namespace,
    logger: logging.Logger,
) -> Dict[str, Any]:
    """Resolve platform-aware ui.run options for Windows and Linux headless targets."""
    gui_cfg = getattr(cfg, "gui", None)
    headless_linux = is_headless_linux()

    config_host = resolve_config_string(
        getattr(gui_cfg, "host", ""),
        logger,
        field_name="gui.host",
        default="",
        allow_empty=True,
    )
    config_port = getattr(gui_cfg, "port", 8080)
    config_open_browser = resolve_config_bool(
        getattr(gui_cfg, "auto_open_browser", False),
        logger,
        field_name="gui.auto_open_browser",
        default=False,
    )
    reverse_proxy_enabled = resolve_config_bool(
        getattr(gui_cfg, "reverse_proxy_enabled", False),
        logger,
        field_name="gui.reverse_proxy_enabled",
        default=False,
    )
    forwarded_allow_ips = resolve_config_string(
        getattr(gui_cfg, "forwarded_allow_ips", "127.0.0.1"),
        logger,
        field_name="gui.forwarded_allow_ips",
        default="127.0.0.1",
        allow_empty=False,
    )
    root_path = resolve_root_path(getattr(gui_cfg, "root_path", ""), logger, default="")
    session_cookie_https_only = resolve_config_bool(
        getattr(gui_cfg, "session_cookie_https_only", False),
        logger,
        field_name="gui.session_cookie_https_only",
        default=False,
    )

    env_host = str(os.environ.get("CVD_HOST", "") or "").strip()
    env_port = os.environ.get("CVD_PORT") or os.environ.get("PORT")
    env_open_browser = parse_optional_bool(os.environ.get("CVD_AUTO_OPEN_BROWSER"))

    default_host = "127.0.0.1" if reverse_proxy_enabled else "0.0.0.0" if headless_linux else "127.0.0.1"
    host = str(args.host or env_host or config_host or default_host).strip()
    port_source = args.port if args.port is not None else env_port if env_port is not None else config_port
    port = resolve_port(port_source, logger)

    open_browser = (
        args.open_browser
        if args.open_browser is not None
        else env_open_browser
        if env_open_browser is not None
        else config_open_browser
    )

    if (
        headless_linux
        and not reverse_proxy_enabled
        and not args.host
        and not env_host
        and host in {"localhost", "127.0.0.1", "::1"}
    ):
        logger.info("Headless Linux detected; overriding local-only host %s with 0.0.0.0", host)
        host = "0.0.0.0"

    show_browser = bool(open_browser)
    if headless_linux and show_browser:
        logger.info("Headless Linux detected; disabling automatic browser launch")
        show_browser = False

    return {
        "host": host,
        "port": port,
        "show": show_browser,
        "headless_linux": headless_linux,
        "reverse_proxy_enabled": reverse_proxy_enabled,
        "forwarded_allow_ips": forwarded_allow_ips,
        "root_path": root_path,
        "session_middleware_kwargs": {"https_only": True} if session_cookie_https_only else None,
    }


def resolve_storage_secret(
    logger: logging.Logger,
    *,
    secret_file: Path | None = None,
) -> str:
    """Resolve a stable storage secret for NiceGUI user storage sessions."""
    env_secret = str(os.environ.get("CVD_STORAGE_SECRET", "") or "").strip()
    if env_secret:
        return env_secret

    resolved_secret_file = secret_file or Path(__file__).with_suffix(".storage_secret")

    try:
        persisted_secret = resolved_secret_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        persisted_secret = ""
    except Exception as exc:
        logger.debug("Could not read storage secret file %s: %s", resolved_secret_file, exc)
        persisted_secret = ""

    if persisted_secret:
        return persisted_secret

    storage_secret = secrets.token_urlsafe(32)
    try:
        _write_storage_secret_file(resolved_secret_file, storage_secret)
    except Exception as exc:
        logger.debug("Could not persist storage secret to file %s: %s", resolved_secret_file, exc)
    return storage_secret


def _write_storage_secret_file(secret_file: Path, storage_secret: str) -> None:
    """Persist a storage secret with owner-only permissions when supported."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(secret_file, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as secret_handle:
            fd = -1
            secret_handle.write(storage_secret)
        if os.name == "posix":
            os.chmod(secret_file, 0o600)
    finally:
        if fd != -1:
            os.close(fd)


def parse_args() -> argparse.Namespace:
    """Kommandozeilenargumente parsen."""
    parser = argparse.ArgumentParser(description="CVD-Tracker")
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to the configuration file",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Host/IP for the web server; overrides config and defaults",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for the web server; overrides config and defaults",
    )
    parser.add_argument(
        "--open-browser",
        dest="open_browser",
        action="store_true",
        help="Open the browser automatically after startup",
    )
    parser.add_argument(
        "--no-open-browser",
        dest="open_browser",
        action="store_false",
        help="Disable automatic browser launch after startup",
    )
    parser.set_defaults(open_browser=None)
    return parser.parse_args()

def setup_exception_handlers(logger: logging.Logger) -> None:
    """Configure consistent exception handlers for the application."""
    
    @app.exception_handler(RuntimeError)
    async def handle_runtime_error(request: Request, exc: RuntimeError) -> JSONResponse:
        """Handle RuntimeError with specific logic for deque mutations."""
        if "deque mutated during iteration" in str(exc):
            logger.warning("Deque mutation detected - handled gracefully to prevent crash")
            return JSONResponse(
                status_code=500,
                content={
                    "error": "internal_processing_error",
                    "message": "Temporary processing issue resolved",
                    "handled": True
                }
            )
        
        logger.error(f"Runtime error: {exc}")
        return JSONResponse(
            status_code=500,
            content={
                "error": "runtime_error", 
                "message": "A runtime error occurred",
                "details": str(exc)
            }
        )

    @app.exception_handler(ValueError)
    async def handle_value_error(request: Request, exc: ValueError) -> JSONResponse:
        """Handle configuration and validation errors."""
        logger.error(f"Value error: {exc}")
        return JSONResponse(
            status_code=400,
            content={
                "error": "configuration_error",
                "message": "Invalid configuration or input",
                "details": str(exc)
            }
        )

    @app.exception_handler(ConnectionError)
    async def handle_connection_error(request: Request, exc: ConnectionError) -> JSONResponse:
        """Handle network and device connection errors."""
        logger.error(f"Connection error: {exc}")
        return JSONResponse(
            status_code=503,
            content={
                "error": "connection_error",
                "message": "Device or network connection failed",
                "details": str(exc)
            }
        )

def create_fallback_logger() -> logging.Logger:
    """Erstellt einen einfachen Fallback-Logger für den Fall, dass die Config nicht verfügbar ist."""
    logger = logging.getLogger("cvd_tracker.fallback")
    
    if not logger.handlers:
        # Console Handler für Fallback
        console_handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%d.%m.%Y %H:%M:%S'
        )
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    
    return logger

def main() -> int:

    """Haupteinstiegspunkt der Anwendung"""
    args = parse_args()
    try:
        # Konfiguration laden und Logger einrichten
        cfg = load_config(args.config)
        logger = get_logger("main")

        logger.info("Starting CVD-Tracker application...")
        logger.info(f"Configuration loaded from {args.config}")

        # Configure exception handlers
        setup_exception_handlers(logger)
        install_asyncio_exception_handler(logger)
        install_nicegui_timer_patch(logger)

        create_gui(config_path=args.config)
        
        # NiceGUI starten
        # Keep app.storage.user stable across restarts when no env secret is configured.
        storage_secret = resolve_storage_secret(logger)
        ui_run_settings = resolve_ui_run_settings(cfg, args, logger)
        logger.info(
            "Starting web UI on %s:%s (platform=%s, headless_linux=%s, auto_open_browser=%s, reverse_proxy_enabled=%s, root_path=%s)",
            ui_run_settings["host"],
            ui_run_settings["port"],
            sys.platform,
            ui_run_settings["headless_linux"],
            ui_run_settings["show"],
            ui_run_settings["reverse_proxy_enabled"],
            ui_run_settings["root_path"] or "/",
        )

        window_title = compute_gui_title(cfg)

        # Remember default favicon for later restore across clients
        try:
            app.storage.general['cvd.default_favicon'] = 'https://www.tuhh.de/favicon.ico'
        except Exception as e:
            logger.debug(f"Could not store default favicon in app storage: {e}")

        ui.run(
            host=ui_run_settings["host"],
            port=ui_run_settings["port"],
            title=window_title,
            favicon='https://www.tuhh.de/favicon.ico',
            show=ui_run_settings["show"],
            reload=False,
            reconnect_timeout=100.0,
            storage_secret=storage_secret,
            session_middleware_kwargs=ui_run_settings["session_middleware_kwargs"],
            proxy_headers=ui_run_settings["reverse_proxy_enabled"],
            forwarded_allow_ips=ui_run_settings["forwarded_allow_ips"],
            root_path=ui_run_settings["root_path"],
        )

    except ImportError as e:
        logger = create_fallback_logger()
        logger.error(f"Import error: {e}")
        logger.error("Please install required dependencies manually: pip install -r requirements.txt")
        return 1
        
    except Exception as e:
        logger = create_fallback_logger()
        logger.error(f"Error occurred at startup: {e}", exc_info=True)
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
