from nicegui import ui, app
from src.gui.layout import build_header, build_footer
from src.gui.init import init_application
from src.gui.instances import get_camera, get_measurement_controller, get_email_system
from src.gui.default_elements.camfeed import create_camfeed_content
from src.gui.default_elements.history_card import create_history_card
from src.gui.default_elements.stats_card import create_stats_card
from src.gui.default_elements.motion_status_element import create_motion_status_element

# Import from settings_elements to avoid duplication
from src.gui.settings_elements.camera_settings import create_uvc_content
from src.gui.settings_elements.motion_detection_settings import create_motiondetection_card
from src.gui.settings_elements.measurement_settings import create_measurement_card
from src.gui.settings_elements.email_settings import create_emailcard

from src.config import get_logger

logger = get_logger('gui.index')

def _compute_title() -> str:
    try:
        title = app.storage.general.get('cvd.title', 'CVD-Tracker')
        return f"{title} - Dashboard"
    except Exception:
        return "CVD-Tracker - Dashboard"

@ui.page('/')
def index_page() -> None:
    """Main dashboard page."""
    
    # 1. Header
    build_header()
    
    # 2. Main Content
    with ui.column().classes('w-full h-full p-4 gap-4'):
        
        # Top Row: Camera Feed & Motion Status
        with ui.row().classes('w-full items-start gap-4'):
             # Camera Feed (Left, larger)
            with ui.column().classes('flex-grow basis-2/3'):
                create_camfeed_content()
            
            # Motion Status (Right, smaller)
            with ui.column().classes('flex-grow basis-1/3'):
                 create_motion_status_element(camera=get_camera(), measurement_controller=get_measurement_controller())

        # Masonry Grid for other cards
        with ui.row().classes('w-full items-start gap-4'):
            
            # Column 1
            with ui.column().classes('flex-1 gap-4 min-w-[300px]'):
                # Stats
                create_stats_card()
                
                # History
                create_history_card()

            # Column 2
            with ui.column().classes('flex-1 gap-4 min-w-[300px]'):
                # Measurement
                with ui.card().classes('w-full'):
                    ui.label('Measurement').classes('text-h6')
                    create_measurement_card(measurement_controller=get_measurement_controller())
                
                # Motion Settings
                with ui.card().classes('w-full'):
                    ui.label('Motion Detection').classes('text-h6')
                    create_motiondetection_card(camera=get_camera())

            # Column 3
            with ui.column().classes('flex-1 gap-4 min-w-[300px]'):
                # UVC Controls
                with ui.card().classes('w-full'):
                    ui.label('Camera Controls').classes('text-h6')
                    create_uvc_content(camera=get_camera())
                
                # Email Settings
                with ui.card().classes('w-full'):
                    ui.label('Email Settings').classes('text-h6')
                    create_emailcard(email_system=get_email_system())

    # 3. Footer
    build_footer()

    # Set title
    ui.run_javascript(f"document.title = '{_compute_title()}'")
