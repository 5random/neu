from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import Request
from nicegui import ui

from .navigation import get_help_sections, load_help_content
from src.gui.constants import StorageKeys
from src.gui.storage import get_ui_pref, set_ui_pref


@ui.page('/help')
def help_page(request: Request) -> None:
    """Render the help page using the settings-style quick-link drawer."""

    payload = load_help_content()
    help_root = payload.get('help') or {}
    title = str(help_root.get('title') or 'Help')
    sections = get_help_sections()

    set_ui_pref(StorageKeys.LAST_ROUTE, '/help')

    from ..layout import build_header, build_footer

    build_header(current_route='/help')

    stored_drawer_open = get_ui_pref(StorageKeys.HELP_DRAWER_OPEN)
    initial_drawer_open = bool(stored_drawer_open) if stored_drawer_open is not None else True
    section_openers: dict[str, Callable[[], None]] = {}
    requested_anchor = str(request.query_params.get('section', '') or '').strip()

    def _scroll_to_section(anchor_id: str) -> None:
        ui.run_javascript(f"""
        (function() {{
            const anchorId = {json.dumps(anchor_id)};
            let attempts = 0;
            const maxAttempts = 20;
            const tryScroll = () => {{
                const target = document.getElementById(anchorId);
                if (target) {{
                    try {{
                        if (window.__cvdHelpQuickLinksSetActiveById) {{
                            window.__cvdHelpQuickLinksSetActiveById(anchorId);
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

    def _open_section(anchor_id: str) -> None:
        opener = section_openers.get(anchor_id)
        if opener is not None:
            opener()
        _scroll_to_section(anchor_id)

    ui.add_head_html(
        """
<style>
html { scroll-behavior: smooth; }
[id] { scroll-margin-top: 80px; }
.cvd-help .prose p { margin: 0.25rem 0; }
.cvd-help .prose { color: inherit; }
.cvd-help .prose a { color: var(--q-primary); }
.cvd-help .prose h1 { font-size: 1.5rem; margin: .75rem 0 .25rem; }
.cvd-help .prose h2 { font-size: 1.25rem; margin: .75rem 0 .25rem; }
.cvd-help .prose h3 { font-size: 1.1rem;  margin: .6rem 0 .2rem; }
.cvd-help .prose h4 { font-size: 1.0rem;  margin: .5rem 0 .2rem; }
.cvd-help .prose h5 { font-size: .95rem;  margin: .4rem 0 .15rem; }
.cvd-help .prose h6 { font-size: .9rem;   margin: .3rem 0 .1rem; }
.cvd-help .q-expansion-item > .q-item {
    border-left: 3px solid var(--q-primary);
    padding-left: 8px;
}
.cvd-help .q-expansion-item .q-item__label { font-weight: 700; letter-spacing: .2px; }
.cvd-sticky { z-index: 10000 !important; }
body .q-tooltip, .q-tooltip { z-index: 11000 !important; }
#cvd-help-links {
    color: inherit;
}
body.body--light #cvd-help-links {
    background: #dbeafe;
}
body.body--dark #cvd-help-links {
    background: #182533;
    border-right: 1px solid rgba(255,255,255,0.08);
}
#cvd-help-links .cvd-help-links-title {
    color: inherit;
}
#cvd-help-links a.cvd-quick-link {
    color: inherit;
    text-decoration: none;
    border-left: 4px solid transparent;
    padding-left: 12px;
    transition: background-color .15s ease, color .15s ease, border-color .15s ease;
}
body.body--light #cvd-help-links a.cvd-quick-link:hover {
    background-color: rgba(59,130,246,0.18);
}
body.body--dark #cvd-help-links a.cvd-quick-link:hover {
    background-color: rgba(96,165,250,0.18);
}
body.body--light #cvd-help-links a.cvd-quick-link.active-link {
    background-color: rgba(59,130,246,0.18);
    border-left-color: var(--q-primary);
    color: #0f172a;
    font-weight: 600;
}
body.body--dark #cvd-help-links a.cvd-quick-link.active-link {
    background-color: rgba(96,165,250,0.18);
    border-left-color: #60a5fa;
    color: #dbeafe;
    font-weight: 600;
}
#cvd-help-links a.cvd-quick-link:focus {
    outline: 2px solid rgba(59,130,246,.6);
    outline-offset: 2px;
}
</style>
"""
    )

    if sections:
        with ui.left_drawer(value=initial_drawer_open).classes('w-64 p-2 cvd-help-links-drawer') as left_drawer:
            left_drawer.props('id=cvd-help-links')
            ui.label('Table of contents').classes('text-bold pl-2 pt-2 cvd-help-links-title')
            for section in sections:
                def _handle_click(_event: Any, _anchor: str = section['anchor_id']) -> None:
                    _open_section(_anchor)
                link = ui.link(section['title'], f"#{section['anchor_id']}") \
                    .classes('cvd-quick-link block px-2 py-1 rounded') \
                    .props(f"data-anchor={section['anchor_id']}")
                link.on(
                    'click',
                    _handle_click,
                    js_handler='(e) => { e.preventDefault(); emit(); }',
                )

        with ui.page_sticky(position='top-left', x_offset=12, y_offset=12).classes('cvd-sticky').style('z-index:10000'):
            drawer_state = initial_drawer_open

            def _toggle_drawer() -> None:
                nonlocal drawer_state
                left_drawer.toggle()
                drawer_state = not drawer_state
                set_ui_pref(StorageKeys.HELP_DRAWER_OPEN, drawer_state)

            ui.button(on_click=_toggle_drawer, icon='menu').props('fab color=primary')

        with ui.page_sticky(position='bottom-right', x_offset=20, y_offset=20).classes('cvd-sticky').style('z-index:10000'):
            ui.button(
                icon='arrow_upward',
                on_click=lambda: ui.run_javascript('window.scrollTo({top:0, behavior:"smooth"})'),
            ).props('fab color=primary').tooltip('Back to top')

    with ui.column().classes('w-full max-w-[1400px] mx-auto gap-4 p-4 pb-24 cvd-help'):
        with ui.row().classes('items-center justify-between'):
            ui.label(title).classes('text-h5 font-semibold')
            ui.button('Back', on_click=lambda: ui.navigate.to('/', new_tab=False)).props('flat').tooltip('Back to Home')

        for section in sections:
            with ui.card().classes('w-full').props(f"id={section['anchor_id']} flat bordered"):
                with ui.expansion(section['title'], value=True, icon='article').props('expand-separator') as expansion:
                    section_openers[section['anchor_id']] = expansion.open
                    if section['route']:
                        ui.link('Open related section in app', section['route']).classes('text-primary text-caption q-mb-xs')
                    ui.markdown(section['content']).classes('prose')

    build_footer()

    ui.add_body_html(
        """
<script>
(function() {
    if (window.__cvdHelpQuickLinksCleanup) {
        try {
            window.__cvdHelpQuickLinksCleanup();
        } catch (e) { /* ignore */ }
        window.__cvdHelpQuickLinksCleanup = null;
    }
    const drawer = document.getElementById('cvd-help-links');
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
    window.__cvdHelpQuickLinksSetActiveById = (anchorId) => {
        const link = map.get(anchorId);
        if (link) activateLink(link);
    };
    links.forEach(link => {
        try {
            const href = link.getAttribute('href') || '';
            const id = decodeURIComponent(href).replace(/^#/, '');
            if (id) map.set(id, link);
        } catch (e) { /* ignore */ }
    });
    const setActiveByHash = () => {
        const hash = decodeURIComponent(window.location.hash || '').replace(/^#/, '');
        const link = map.get(hash);
        if (link) activateLink(link);
    };
    const onHashChange = () => setActiveByHash();
    window.addEventListener('hashchange', onHashChange);
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const link = map.get(entry.target.id);
                if (link) activateLink(link);
            }
        });
    }, { root: null, rootMargin: '-40% 0px -55% 0px', threshold: 0.01 });
    Array.from(map.keys()).forEach(id => {
        const element = document.getElementById(id);
        if (element) observer.observe(element);
    });
    setActiveByHash();
    if (!links.some(link => link.classList.contains('active-link')) && links.length) {
        activateLink(links[0]);
    }
    window.__cvdHelpQuickLinksCleanup = () => {
        window.removeEventListener('hashchange', onHashChange);
        window.__cvdHelpQuickLinksSetActiveById = null;
        observer.disconnect();
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

        if anchor_id in section_openers:
            _open_section(anchor_id)

    ui.timer(0.2, _open_requested_section_on_load, once=True, immediate=False)
