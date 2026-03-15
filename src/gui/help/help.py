from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
import re

from nicegui import ui
from src.gui.constants import StorageKeys
from src.gui.storage import set_ui_pref
try:
    import yaml
except ImportError:  # graceful fallback if PyYAML is not installed
    yaml = None  # type: ignore[assignment]

# Links are provided by help.yaml via an optional 'link' field per section.


def _slugify(title: str) -> str:
    """Create a URL-safe anchor id from a title."""
    
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s or "section"


def _load_yaml_content() -> Dict[str, Any]:
    """Load help.yaml content.

    Returns a dict like:
    { 'help': { 'title': str, 'sections': [ { 'title': str, 'content': str }, ... ] } }
    """
    help_yaml_path = Path(__file__).parent / 'help.yaml'
    if not help_yaml_path.exists():
        return { 'help': { 'title': 'Help', 'sections': [] } }

    try:
        text = help_yaml_path.read_text(encoding='utf-8')
    except Exception:
        return { 'help': { 'title': 'Help', 'sections': [] } }

    if yaml is None:
        # Fallback: minimal parser – put full text into a single section
        return {
            'help': {
                'title': 'Help',
                'sections': [
                    {
                        'title': 'Raw help.yaml',
                        'content': text,
                    }
                ],
            }
        }

    try:
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError('Invalid YAML structure')
        return data
    except Exception:
        # On parse error, show raw content
        return {
            'help': {
                'title': 'Help',
                'sections': [
                    {
                        'title': 'Raw help.yaml',
                        'content': text,
                    }
                ],
            }
        }


@ui.page('/help')
def help_page() -> None:
    """Help page rendering help.yaml with anchors and quick links."""
    payload = _load_yaml_content()
    help_root = payload.get('help') or {}
    title: str = help_root.get('title') or 'Help'
    sections: List[Dict[str, Any]] = help_root.get('sections') or []

    # Persist last visited route for this browser session
    set_ui_pref(StorageKeys.LAST_ROUTE, '/help')

    from ..layout import build_header, build_footer
    build_header()

    # Minor CSS for anchors and readability
    ui.add_head_html(
        """
<style>
html { scroll-behavior: smooth; }
[id] { scroll-margin-top: 80px; }
.cvd-help .prose p { margin: 0.25rem 0; }
/* Respect current theme colors */
.cvd-help .prose { color: inherit; }
.cvd-help .prose a { color: var(--q-primary); }
/* Stronger section headings */
.cvd-help .section-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.cvd-help .section-title { font-weight: 700; letter-spacing: 0.2px; }
.cvd-help .section-title-accent { border-left: 3px solid var(--q-primary); padding-left: 8px; }
/* Tame markdown heading sizes for better hierarchy */
.cvd-help .prose h1 { font-size: 1.5rem; margin: .75rem 0 .25rem; }
.cvd-help .prose h2 { font-size: 1.25rem; margin: .75rem 0 .25rem; }
.cvd-help .prose h3 { font-size: 1.1rem;  margin: .6rem 0 .2rem; }
.cvd-help .prose h4 { font-size: 1.0rem;  margin: .5rem 0 .2rem; }
.cvd-help .prose h5 { font-size: .95rem;  margin: .4rem 0 .15rem; }
.cvd-help .prose h6 { font-size: .9rem;   margin: .3rem 0 .1rem; }
/* Style expansion headers to look like strong section titles */
.cvd-help .q-expansion-item > .q-item { border-left: 3px solid var(--q-primary); padding-left: 8px; }
.cvd-help .q-expansion-item .q-item__label { font-weight: 700; letter-spacing: .2px; }
</style>
"""
    )

    with ui.column().classes('w-full gap-4 p-4 cvd-help'):
        with ui.row().classes('items-center justify-between'):
            ui.label(title).classes('text-h5 font-semibold')
            ui.button('Back', on_click=lambda: ui.navigate.to('/', new_tab=False)).props('flat').tooltip('Back to Home')

        if sections:
            # Local table of contents (anchors on this page)
            with ui.expansion('Table of contents', icon='list').props('expand-separator').classes('w-full'):
                with ui.column().classes('gap-1'):
                    for s in sections:
                        stitle = str(s.get('title') or '')
                        sid = _slugify(stitle)
                        with ui.row().classes('items-center gap-2'):
                            ui.label('•')
                            ui.link(stitle or 'Section', f"#%s" % sid)

        # Render sections (collapsible, default expanded)
        for section in sections:
            stitle = str(section.get('title') or 'Section')
            content = str(section.get('content') or '')
            route = section.get('link') or ''
            sid = _slugify(stitle)

            with ui.card().classes('w-full'):
                # Anchor element for smooth scrolling to this section
                ui.html(f'<span id="{sid}"></span>', sanitize=False)
                with ui.expansion(stitle, value=True, icon='article').props('expand-separator'):
                    if isinstance(route, str) and route.strip():
                        ui.link('Open related section in app', route.strip()).classes('text-primary text-caption q-mb-xs')
                    ui.markdown(content).classes('prose')

    build_footer()
