from typing import Optional, TYPE_CHECKING, Callable, Any
import json

from fastapi import Request
from nicegui import ui

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
from src.gui.constants import StorageKeys
from src.gui.storage import delete_ui_pref, get_runtime_ui_pref, get_ui_pref, set_runtime_ui_pref, set_ui_pref

logger = get_logger("settings_page")


def _get_core_instances() -> tuple[Optional[Camera], Optional['MeasurementController'], Optional[EMailSystem]]:
    """Retrieve core instances from non-persistent registry to avoid JSON persistence issues."""
    try:
        return get_instances()
    except Exception:
        return None, None, None


@ui.page('/settings')
def settings_page(request: Request) -> None:
    """Settings page with left quick links and stacked sections.

    Sections in order: Camera, Motion Detection, Measurement, Email.
    The header/footer are provided by the main app; here we only define content.
    """
    logger.info('Opening settings page')

    camera, measurement_controller, email_system = _get_core_instances()
    cfg = get_global_config()
    if cfg is None:
        ui.notify('Configuration not loaded', type='warning', position='bottom-right')
    # Persist last visited route for this browser session
    set_ui_pref(StorageKeys.LAST_ROUTE, '/settings')

    # Hinweis: Der Browser-Tab-Titel wird ausschließlich beim Start über ui.run gesetzt.
    # Auf der Settings-Seite erfolgt keine dynamische Anpassung mehr.

    # Gemeinsamen Header/Footer der App verwenden
    from .layout import build_header, build_footer
    build_header()

    # Determine initial drawer state from persisted UI storage (default: open)
    _stored_drawer_open = get_ui_pref(StorageKeys.DRAWER_OPEN)
    _initial_drawer_open = bool(_stored_drawer_open) if _stored_drawer_open is not None else True

    section_openers: dict[str, Callable[[], None]] = {}
    anchor_to_section: dict[str, str] = {}
    requested_anchor = str(request.query_params.get('section', '') or '').strip()

    def _scroll_to_section(anchor_id: str, section_id: str) -> None:
        ui.run_javascript(f"""
        (function() {{
            const anchorId = {json.dumps(anchor_id)};
            const sectionId = {json.dumps(section_id)};
            const isCollapsed = () => {{
                const content = document.getElementById(`cvd-content-${{sectionId}}`);
                if (!content) return false;
                const style = window.getComputedStyle(content);
                return content.classList.contains('hidden') || style.display === 'none' || style.visibility === 'hidden';
            }};
            let attempts = 0;
            const maxAttempts = 40;
            const tryScroll = () => {{
                const target = document.getElementById(anchorId);
                const sectionReady = !isCollapsed();
                if (target && sectionReady) {{
                    try {{
                        if (window.__cvdQuickLinksSetActiveById) {{
                            window.__cvdQuickLinksSetActiveById(anchorId);
                        }}
                        history.replaceState(null, '', `#${{anchorId}}`);
                    }} catch (e) {{ /* ignore */ }}
                    target.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
                    return true;
                }}
                attempts += 1;
                return attempts >= maxAttempts;
            }};
            if (tryScroll()) return;
            const timer = window.setInterval(() => {{
                if (tryScroll()) {{
                    window.clearInterval(timer);
                }}
            }}, 75);
        }})();
        """)

    def _open_section(anchor_id: str, section_id: str) -> None:
        opener = section_openers.get(section_id)
        if opener is not None:
            opener()
        _scroll_to_section(anchor_id, section_id)

    # Left drawer with quick links; initialize model value explicitly
    with ui.left_drawer(value=_initial_drawer_open).classes('w-64 p-2 cvd-quicklinks-drawer') as left_drawer:
        def _quick_link(label: str, anchor_id: str, section_id: str) -> None:
            anchor_to_section[anchor_id] = section_id
            def _handle_click(_event: Any, _anchor: str = anchor_id, _section: str = section_id) -> None:
                _open_section(_anchor, _section)
            link = ui.link(label, f'#{anchor_id}') \
                .classes('cvd-quick-link block px-2 py-1 rounded') \
                .props(f'data-anchor={anchor_id} data-section={section_id}')
            link.on(
                'click',
                _handle_click,
                js_handler='(e) => { e.preventDefault(); emit(); }',
            )

        # Add an id to scope our script and styles
        left_drawer.props('id=cvd-quicklinks')
        ui.label('Quick Links').classes('text-bold pl-2 pt-2 cvd-quicklinks-title')
        # Anchor links to scroll to sections and expand the owning card if necessary
        _quick_link('Camera', 'camera', 'camera')
        _quick_link('Motion Detection', 'motion', 'camera')
        _quick_link('Measurement', 'measurement', 'measurement')
        _quick_link('E-Mail', 'email', 'email')
        _quick_link('Configuration', 'config', 'config')
        _quick_link('Metadata', 'metadata', 'metadata')
        _quick_link('Update', 'update', 'update')
        _quick_link('Logs', 'logs', 'logs')

    # Sticky menu button to toggle the left drawer for navigation
    with ui.page_sticky(position='top-left', x_offset=12, y_offset=12).classes('cvd-sticky').style('z-index:10000'):
        # Track state locally to ensure we persist correct value
        _drawer_state = _initial_drawer_open

        def _toggle_drawer() -> None:
            nonlocal _drawer_state
            left_drawer.toggle()
            _drawer_state = not _drawer_state
            set_ui_pref(StorageKeys.DRAWER_OPEN, _drawer_state)
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

            # Reset persisted UI preferences with confirmation
            reset_dialog = ui.dialog()
            with reset_dialog:
                with ui.card().classes('items-start gap-3'):
                    ui.label('Reset UI Preferences?').classes('text-h6')
                    ui.label('This will clear persisted UI preferences like dark mode, last visited page, drawer state, and collapsed sections for this browser.').classes('text-body2')
                    with ui.row().classes('gap-2'):
                        def _confirm_reset() -> None:
                            try:
                                for key in [
                                    StorageKeys.DARK_MODE,
                                    StorageKeys.LAST_ROUTE,
                                    StorageKeys.DRAWER_OPEN,
                                    StorageKeys.HELP_DRAWER_OPEN,
                                    StorageKeys.COLLAPSE_STATE,
                                    StorageKeys.GUI_TITLE,
                                ]:
                                    delete_ui_pref(key)
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

    # Prepare runtime-scoped collapse state storage.
    # All cards start collapsed after an app restart, but keep the last user choice while the app runs.
    collapse_state = get_runtime_ui_pref(StorageKeys.COLLAPSE_STATE, {}) or {}
    if not isinstance(collapse_state, dict):
        collapse_state = {}

    def _collapsible_card(section_id: str, title: str, default_collapsed: bool = True) -> ui.column:
        """Create a collapsible card with runtime-scoped UI state.

        Returns the content container to populate inside the card.
        """
        collapsed = bool(collapse_state.get(section_id, default_collapsed))
        
        # Issue #7 fix: Validate section_id and escape title
        import re
        import html
        if not re.match(r'^[a-zA-Z0-9_-]+$', section_id):
            logger.warning(f"Invalid section_id '{section_id}', using 'section'")
            section_id = 'section'
        title_escaped = html.escape(title)

        with ui.card().classes('w-full').props(f'flat bordered id=cvd-card-{section_id}'):
            # Header with ID (for anchor/IntersectionObserver) and toggle
            with ui.row().classes('items-center justify-between'):
                ui.html(
                    f'<div id="{section_id}" class="text-h6 font-semibold mb-2">{title_escaped}</div>',
                    sanitize=False,
                )

                def _toggle() -> None:
                    nonlocal collapsed
                    collapsed = not collapsed
                    collapse_state[section_id] = collapsed
                    set_runtime_ui_pref(StorageKeys.COLLAPSE_STATE, collapse_state)
                    content.visible = not collapsed
                    chevron.props('icon=' + ('chevron_right' if collapsed else 'expand_more'))

                def _ensure_open() -> None:
                    if collapsed:
                        _toggle()

                chevron = ui.button(icon=('chevron_right' if collapsed else 'expand_more'), on_click=_toggle)
                chevron.props(f'flat round dense id=cvd-toggle-{section_id}').tooltip('Collapse/expand')

            # Content container
            content = ui.column().classes('w-full gap-4')
            content.props(f'id=cvd-content-{section_id}')
            content.visible = not collapsed
            section_openers[section_id] = _ensure_open
        return content

    def _collapsible_lazy_card(section_id: str, title: str, render_fn: Callable[[Any], None], default_collapsed: bool = True) -> ui.column:
        """Create a collapsible card that renders its content lazily on first expand.

        render_fn receives a NiceGUI container to populate when needed.
        """
        collapsed = bool(collapse_state.get(section_id, default_collapsed))
        rendered = False
        with ui.card().classes('w-full').props(f'flat bordered id=cvd-card-{section_id}'):
            with ui.row().classes('items-center justify-between'):
                ui.html(
                    f'<div id="{section_id}" class="text-h6 font-semibold mb-2">{title}</div>',
                    sanitize=False,
                )

                def _toggle() -> None:
                    nonlocal collapsed, rendered
                    collapsed = not collapsed
                    collapse_state[section_id] = collapsed
                    set_runtime_ui_pref(StorageKeys.COLLAPSE_STATE, collapse_state)
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

                def _ensure_open() -> None:
                    if collapsed:
                        _toggle()

                chevron = ui.button(icon=('chevron_right' if collapsed else 'expand_more'), on_click=_toggle)
                chevron.props(f'flat round dense id=cvd-toggle-{section_id}').tooltip('Collapse/expand')

            content = ui.column().classes('w-full gap-4')
            content.props(f'id=cvd-content-{section_id}')
            content.visible = not collapsed
            section_openers[section_id] = _ensure_open
            if not collapsed and not rendered:
                try:
                    with content:
                        render_fn(content)
                    rendered = True
                except Exception:
                    pass
            return content

    # Main content: stacked sections similar to VS Code settings
    # Center the content and constrain to a comfortable reading width
    with ui.column().classes('w-full max-w-[1400px] mx-auto gap-4 p-4 pb-24'):
        # Camera section: 2-column layout (lazy render)
        def _render_camera(_container: Any) -> None:
            with ui.grid(columns=2).classes('w-full gap-4 items-start').style('grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));'):
                with ui.column().classes('gap-3 min-w-0 w-full self-start'):
                    create_camfeed_content(camera)
                    ui.separator()
                    ui.label('Motion Detection').classes('text-subtitle1 font-semibold').props('id=motion')
                    create_motiondetection_card(camera)
                with ui.column().classes('gap-3 min-w-0 w-full self-start'):
                    create_uvc_content(camera)

        # Default heavy camera section to collapsed on startup (renders lazily)
        _collapsible_lazy_card('camera', 'Camera', _render_camera, default_collapsed=True)

        measurement_card = _collapsible_card('measurement', 'Measurement', default_collapsed=True)
        with measurement_card:
            create_measurement_card(measurement_controller=measurement_controller)

        email_card = _collapsible_card('email', 'E-Mail Notifications', default_collapsed=True)
        with email_card:
            create_emailcard(email_system=email_system)

        metadata_card = _collapsible_card('metadata', 'Metadata', default_collapsed=True)
        with metadata_card:
            create_metadata_settings()

        config_card = _collapsible_card('config', 'Configuration', default_collapsed=True)
        with config_card:
            create_config_settings()

        update_card = _collapsible_card('update', 'Update', default_collapsed=True)
        with update_card:
            create_update_settings()

        def _render_logs(_container: Any) -> None:
            create_log_settings()
        # Logs can be heavy; default to collapsed
        _collapsible_lazy_card('logs', 'Logs', _render_logs, default_collapsed=True)

    # No local footer: global footer is used
    if build_footer is not None:
        build_footer()
    
    # Smooth scrolling and active link highlighting for quick links
    # Inject CSS/JS properly via NiceGUI (scripts are not allowed inside ui.html)
    ui.add_head_html(
        """
<style>
html { scroll-behavior: smooth; }
/* Offset for fixed header; adjust if header height changes */
[id] { scroll-margin-top: 80px; }
/* Theme-aware settings drawer */
#cvd-quicklinks {
    color: inherit;
}
body.body--light #cvd-quicklinks {
    background: #dbeafe;
}
body.body--dark #cvd-quicklinks {
    background: #182533;
    border-right: 1px solid rgba(255,255,255,0.08);
}
#cvd-quicklinks .cvd-quicklinks-title {
    color: inherit;
}
#cvd-quicklinks a.cvd-quick-link {
    color: inherit;
    text-decoration: none;
    border-left: 4px solid transparent;
    padding-left: 12px;
    transition: background-color .15s ease, color .15s ease, border-color .15s ease;
}
body.body--light #cvd-quicklinks a.cvd-quick-link:hover {
    background-color: rgba(59,130,246,0.18);
}
body.body--dark #cvd-quicklinks a.cvd-quick-link:hover {
    background-color: rgba(96,165,250,0.18);
}
/* Active link highlight */
body.body--light #cvd-quicklinks a.cvd-quick-link.active-link {
    background-color: rgba(59,130,246,0.18);
    border-left-color: var(--q-primary);
    color: #0f172a;
    font-weight: 600;
}
body.body--dark #cvd-quicklinks a.cvd-quick-link.active-link {
    background-color: rgba(96,165,250,0.18);
    border-left-color: #60a5fa;
    color: #dbeafe;
    font-weight: 600;
}
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
    if (window.__cvdQuickLinksCleanup) {
        try {
            window.__cvdQuickLinksCleanup();
        } catch (e) { /* ignore */ }
        window.__cvdQuickLinksCleanup = null;
    }
    const drawer = document.getElementById('cvd-quicklinks');
    if (!drawer) return;
    const links = Array.from(drawer.querySelectorAll('a.cvd-quick-link[href^="#"]'));
    const map = new Map();
    const activateLink = (link) => {
        if (!link) return;
        links.forEach(l => {
            const active = l === link;
            l.classList.toggle('active-link', active);
            if (active) {
                l.setAttribute('aria-current', 'location');
            } else {
                l.removeAttribute('aria-current');
            }
        });
        try {
            link.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        } catch (e) { /* ignore */ }
    };
    window.__cvdQuickLinksSetActiveById = (anchorId) => {
        const link = map.get(anchorId);
        if (link) activateLink(link);
    };
    links.forEach(a => {
        try {
            const href = a.getAttribute('href') || '';
            const id = decodeURIComponent(href).replace(/^#/, '');
            if (id) map.set(id, a);
        } catch (e) { /* ignore */ }
    });
    const setActiveByHash = () => {
        const h = decodeURIComponent(window.location.hash || '').replace(/^#/, '');
        const el = map.get(h);
        if (el) activateLink(el);
    };
    const onHashChange = () => setActiveByHash();
    window.addEventListener('hashchange', onHashChange);
    const opts = { root: null, rootMargin: '-40% 0px -55% 0px', threshold: 0.01 };
    const obs = new IntersectionObserver((entries) => {
        entries.forEach(e => {
            if (e.isIntersecting) {
                const id = e.target.id;
                const link = map.get(id);
                if (link) activateLink(link);
            }
        });
    }, opts);
    Array.from(map.keys()).forEach(id => {
        const el = document.getElementById(id);
        if (el) obs.observe(el);
    });
    setActiveByHash();
    if (!links.some(link => link.classList.contains('active-link')) && links.length) {
        activateLink(links[0]);
    }
    window.__cvdQuickLinksCleanup = () => {
        window.removeEventListener('hashchange', onHashChange);
        window.__cvdQuickLinksSetActiveById = null;
        obs.disconnect();
    };
})();
</script>
"""
    )

    async def _open_requested_section_on_load() -> None:
        anchor_id = requested_anchor
        if not anchor_id:
            try:
                hash_value = await ui.run_javascript(
                    "return decodeURIComponent(window.location.hash || '').replace(/^#/, '')",
                    timeout=2.0,
                )
            except Exception:
                hash_value = ''
            anchor_id = str(hash_value or '').strip()

        section_id = anchor_to_section.get(anchor_id)
        if section_id:
            _open_section(anchor_id, section_id)

    ui.timer(0.2, _open_requested_section_on_load, once=True, immediate=False)
