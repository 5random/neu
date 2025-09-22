from typing import Optional, TYPE_CHECKING

from nicegui import ui, app

from src.cam.camera import Camera
if TYPE_CHECKING:
    from src.measurement import MeasurementController
from src.notify import EMailSystem
from .instances import get_instances
from src.config import get_global_config, get_logger

from src.gui.settings_elements.camera_settings import create_uvc_content
from src.gui.settings_elements.motion_detection_settings import create_motiondetection_card
from src.gui.settings_elements.measurement_settings import create_measurement_card
from src.gui.settings_elements.email_settings import create_emailcard
from src.gui.settings_elements.camfeed_settings import create_camfeed_content
from src.gui.settings_elements.log_settings import create_log_settings
from src.gui.settings_elements.config_settings import create_config_settings
from src.gui.settings_elements.update_settings import create_update_settings
from src.gui.settings_elements.metadata_settings import create_metadata_settings

logger = get_logger("settings_page")


def _get_core_instances() -> tuple[Optional[Camera], Optional['MeasurementController'], Optional[EMailSystem]]:
    """Retrieve core instances from non-persistent registry to avoid JSON persistence issues."""
    try:
        return get_instances()
    except Exception:
        return None, None, None


@ui.page('/settings')
def settings_page() -> None:
    """Settings page with left quick links and stacked sections.

    Sections in order: Camera, Motion Detection, Measurement, Email.
    The header/footer are provided by the main app; here we only define content.
    """
    logger.info('Opening settings page')

    camera, measurement_controller, email_system = _get_core_instances()
    cfg = get_global_config()
    if cfg is None:
        ui.notify('Configuration not loaded', type='warning', position='bottom-right')
    # Persist last visited route per client
    try:
        app.storage.client['cvd.last_route'] = '/settings'
    except Exception:
        pass

    # Gemeinsamen Header/Footer der App verwenden
    try:
        from .gui_ import build_header, build_footer  # type: ignore
    except Exception:
        build_header = None  # type: ignore
        build_footer = None  # type: ignore

    if build_header:
        build_header()

    # Determine initial drawer state from per-client storage (default: open)
    try:
        _stored_drawer_open = app.storage.client.get('cvd.settings.drawer_open')
        _initial_drawer_open = bool(_stored_drawer_open) if _stored_drawer_open is not None else True
    except Exception:
        _initial_drawer_open = True

    # Left drawer with quick links; initialize model value explicitly
    with ui.left_drawer(value=_initial_drawer_open).classes('bg-blue-100 w-64 p-2') as left_drawer:
        # Add an id to scope our script and styles
        left_drawer.props('id=cvd-quicklinks')
        ui.label('Quick Links').classes('text-bold pl-2 pt-2')
        # Anchor links to scroll to sections
        ui.link('Camera', '#camera').classes('cvd-quick-link block px-2 py-1 hover:bg-blue-200 rounded')
        ui.link('Metadata', '#metadata').classes('cvd-quick-link block px-2 py-1 hover:bg-blue-200 rounded')
        ui.link('Motion Detection', '#motion').classes('cvd-quick-link block px-2 py-1 hover:bg-blue-200 rounded')
        ui.link('Measurement', '#measurement').classes('cvd-quick-link block px-2 py-1 hover:bg-blue-200 rounded')
        ui.link('E-Mail', '#email').classes('cvd-quick-link block px-2 py-1 hover:bg-blue-200 rounded')
        ui.link('Configuration', '#config').classes('cvd-quick-link block px-2 py-1 hover:bg-blue-200 rounded')
        ui.link('Update', '#update').classes('cvd-quick-link block px-2 py-1 hover:bg-blue-200 rounded')
        ui.link('Logs', '#logs').classes('cvd-quick-link block px-2 py-1 hover:bg-blue-200 rounded')

    # Sticky menu button to toggle the left drawer for navigation
    with ui.page_sticky(position='top-left', x_offset=12, y_offset=12).classes('cvd-sticky').style('z-index:10000'):
        # Track state locally to ensure we persist correct value
        _drawer_state = _initial_drawer_open

        def _toggle_drawer():
            nonlocal _drawer_state
            left_drawer.toggle()
            _drawer_state = not _drawer_state
            # Persist per-client drawer state
            try:
                app.storage.client['cvd.settings.drawer_open'] = _drawer_state
            except Exception:
                pass
        ui.button(on_click=_toggle_drawer, icon='menu').props('fab color=primary')

    # Sticky actions: Back-to-top and optional help/contact
    with ui.page_sticky(position='bottom-right', x_offset=20, y_offset=20).classes('cvd-sticky').style('z-index:10000'):
        with ui.column().classes('gap-2'):
            ui.button(
                icon='arrow_upward',
                on_click=lambda: ui.run_javascript('window.scrollTo({top:0, behavior:"smooth"})'),
            ).props('fab color=primary').tooltip('Back to top')
            ui.button(on_click=lambda: ui.navigate.to('/help'), icon='contact_support').props('fab')\
                .tooltip('Help')

            # Reset UI Preferences (client storage) with confirmation
            reset_dialog = ui.dialog()
            with reset_dialog:
                with ui.card().classes('items-start gap-3'):
                    ui.label('Reset UI Preferences?').classes('text-h6')
                    ui.label('This will clear per-client preferences like dark mode, last visited page, drawer state, and collapsed sections for this browser.').classes('text-body2')
                    with ui.row().classes('gap-2'):
                        def _confirm_reset():
                            try:
                                # Clear known client-scoped keys
                                for key in ['cvd.dark_mode', 'cvd.last_route', 'cvd.settings.drawer_open', 'cvd.settings.collapse']:
                                    try:
                                        if key in app.storage.client:
                                            del app.storage.client[key]
                                    except Exception:
                                        pass
                                ui.notify('UI preferences reset', type='positive', position='bottom-right')
                                reset_dialog.close()
                                # Reload to apply fresh state
                                ui.run_javascript('window.location.reload()')
                            except Exception:
                                reset_dialog.close()
                                ui.notify('Failed to reset preferences', type='negative', position='bottom-right')
                        ui.button('Reset', on_click=_confirm_reset).props('color=negative')
                        ui.button('Cancel', on_click=reset_dialog.close)

            ui.button(icon='settings_backup_restore', on_click=reset_dialog.open).props('fab color=warning').tooltip('Reset UI preferences for this browser')

    # Prepare per-client collapse state storage
    try:
        collapse_state = app.storage.client.get('cvd.settings.collapse') or {}
        if not isinstance(collapse_state, dict):
            collapse_state = {}
    except Exception:
        collapse_state = {}

    def _collapsible_card(section_id: str, title: str, default_collapsed: bool = False):
        """Create a collapsible card with persistent (per client) state.

        Returns the content container to populate inside the card.
        """
        collapsed = bool(collapse_state.get(section_id, default_collapsed))
        with ui.card().classes('w-full').props('flat bordered'):
            # Header with ID (for anchor/IntersectionObserver) and toggle
            with ui.row().classes('items-center justify-between'):
                ui.html(f'<div id="{section_id}" class="text-h6 font-semibold mb-2">{title}</div>')

                def _toggle():
                    nonlocal collapsed
                    collapsed = not collapsed
                    collapse_state[section_id] = collapsed
                    try:
                        app.storage.client['cvd.settings.collapse'] = collapse_state
                    except Exception:
                        pass
                    content.visible = not collapsed
                    chevron.props('icon=' + ('chevron_right' if collapsed else 'expand_more'))

                chevron = ui.button(icon=('chevron_right' if collapsed else 'expand_more'), on_click=_toggle)
                chevron.props('flat round dense').tooltip('Collapse/expand')

            # Content container
            content = ui.column().classes('w-full gap-4')
            content.visible = not collapsed
        return content

    def _collapsible_lazy_card(section_id: str, title: str, render_fn, default_collapsed: bool = False):
        """Create a collapsible card that renders its content lazily on first expand.

        render_fn receives a NiceGUI container to populate when needed.
        """
        collapsed = bool(collapse_state.get(section_id, default_collapsed))
        rendered = False
        with ui.card().classes('w-full').props('flat bordered'):
            with ui.row().classes('items-center justify-between'):
                ui.html(f'<div id="{section_id}" class="text-h6 font-semibold mb-2">{title}</div>')

                def _toggle():
                    nonlocal collapsed, rendered
                    collapsed = not collapsed
                    collapse_state[section_id] = collapsed
                    try:
                        app.storage.client['cvd.settings.collapse'] = collapse_state
                    except Exception:
                        pass
                    content.visible = not collapsed
                    chevron.props('icon=' + ('chevron_right' if collapsed else 'expand_more'))
                    if not collapsed and not rendered:
                        # Lazy build on first expand
                        try:
                            with content:
                                render_fn(content)
                            rendered = True
                        except Exception:
                            pass

                chevron = ui.button(icon=('chevron_right' if collapsed else 'expand_more'), on_click=_toggle)
                chevron.props('flat round dense').tooltip('Collapse/expand')

            content = ui.column().classes('w-full gap-4')
            content.visible = not collapsed
            if not collapsed and not rendered:
                try:
                    with content:
                        render_fn(content)
                    rendered = True
                except Exception:
                    pass

    # Main content: stacked sections similar to VS Code settings
    # Center the content and constrain to a comfortable reading width
    with ui.column().classes('w-full max-w-[1400px] mx-auto gap-4 p-4 pb-24'):
        # Camera section: 2-column layout (lazy render)
        def _render_camera(_container):
            with ui.grid(columns=2).classes('w-full gap-4'):
                with ui.column().classes('gap-3'):
                    create_camfeed_content(camera)
                    ui.separator()
                    ui.label('Motion Detection').classes('text-subtitle1 font-semibold').props('id=motion')
                    create_motiondetection_card(camera)
                with ui.column().classes('gap-3'):
                    create_uvc_content(camera)

        # Default heavy camera section to collapsed on first visit (renders lazily)
        _collapsible_lazy_card('camera', 'Camera', _render_camera, default_collapsed=True)

        measurement_card = _collapsible_card('measurement', 'Measurement')
        with measurement_card:
            create_measurement_card(measurement_controller=measurement_controller)

        email_card = _collapsible_card('email', 'E-Mail Notifications')
        with email_card:
            create_emailcard(email_system=email_system)

        metadata_card = _collapsible_card('metadata', 'Metadata')
        with metadata_card:
            create_metadata_settings()

        config_card = _collapsible_card('config', 'Configuration')
        with config_card:
            create_config_settings()

        update_card = _collapsible_card('update', 'Update')
        with update_card:
            create_update_settings()

        def _render_logs(_container):
            create_log_settings()
        # Logs can be heavy; default to collapsed
        _collapsible_lazy_card('logs', 'Logs', _render_logs, default_collapsed=True)

    # No local footer: global footer is used
    if build_footer:
        build_footer()
    
    # Smooth scrolling and active link highlighting for quick links
    # Inject CSS/JS properly via NiceGUI (scripts are not allowed inside ui.html)
    try:
        _assets_loaded = app.storage.client.get('cvd.settings.assets_loaded')
    except Exception:
        _assets_loaded = False
    if not _assets_loaded:
        ui.add_head_html(
        """
<style>
html { scroll-behavior: smooth; }
/* Offset for fixed header; adjust if header height changes */
[id] { scroll-margin-top: 80px; }
/* Active link highlight */
#cvd-quicklinks a.cvd-quick-link.active-link { background-color: rgba(59,130,246,0.25); }
#cvd-quicklinks a.cvd-quick-link { transition: background-color .15s ease; }
/* Improve focus visibility */
#cvd-quicklinks a.cvd-quick-link:focus { outline: 2px solid rgba(59,130,246,.6); outline-offset: 2px; }
/* Ensure sticky controls are always above content */
.cvd-sticky { z-index: 10000 !important; }
/* Ensure tooltips from Quasar/NiceGUI render above all content (including sticky controls) */
body .q-tooltip, .q-tooltip { z-index: 11000 !important; }
</style>
"""
        )
    ui.add_body_html(
        """
<script>
(function() {
    if (window.__cvdQuickLinksSetup) return; // prevent duplicate init
    window.__cvdQuickLinksSetup = true;
    const drawer = document.getElementById('cvd-quicklinks');
    if (!drawer) return;
    const links = Array.from(drawer.querySelectorAll('a.cvd-quick-link[href^="#"]'));
    const map = new Map();
    links.forEach(a => {
        try {
            const href = a.getAttribute('href') || '';
            const id = decodeURIComponent(href).replace(/^#/, '');
            if (id) map.set(id, a);
            a.addEventListener('click', () => {
                links.forEach(l => l.classList.remove('active-link'));
                a.classList.add('active-link');
            });
        } catch (e) { /* ignore */ }
    });
    const setActiveByHash = () => {
        const h = decodeURIComponent(window.location.hash || '').replace(/^#/, '');
        const el = map.get(h);
        if (el) {
            links.forEach(l => l.classList.remove('active-link'));
            el.classList.add('active-link');
        }
    };
    window.addEventListener('hashchange', setActiveByHash);
    const opts = { root: null, rootMargin: '-40% 0px -55% 0px', threshold: 0.01 };
    const obs = new IntersectionObserver((entries) => {
        entries.forEach(e => {
            if (e.isIntersecting) {
                const id = e.target.id;
                const link = map.get(id);
                if (link) {
                    links.forEach(l => l.classList.remove('active-link'));
                    link.classList.add('active-link');
                }
            }
        });
    }, opts);
    Array.from(map.keys()).forEach(id => {
        const el = document.getElementById(id);
        if (el) obs.observe(el);
    });
    setActiveByHash();
})();
</script>
"""
    )
    try:
        app.storage.client['cvd.settings.assets_loaded'] = True
    except Exception:
        pass

    # Also clear UI preferences on client disconnect for this page/session
    try:
        client = ui.context.client
        def _reset_prefs_on_disconnect() -> None:
            try:
                for key in ['cvd.dark_mode', 'cvd.last_route', 'cvd.settings.drawer_open', 'cvd.settings.collapse']:
                    try:
                        if key in app.storage.client:
                            del app.storage.client[key]
                    except Exception:
                        pass
            except Exception:
                pass
        client.on_disconnect(_reset_prefs_on_disconnect)
    except Exception:
        pass


