from nicegui import ui

from src.config import get_logger
from src.gui.default_elements.camfeed import create_camfeed_content
from src.gui.default_elements.history_card import create_history_card
from src.gui.default_elements.measurementcard import create_measurement_card
from src.gui.default_elements.motion_status_element import create_motion_status_element
from src.gui.default_elements.stats_card import create_stats_card
from src.gui.instances import get_camera, get_email_system, get_measurement_controller
from src.gui.layout import build_footer, build_header

logger = get_logger('gui.index')
SHOW_DASHBOARD_ACTIVE_GROUP_SELECTOR = True
SHOW_DASHBOARD_ALERT_HISTORY = True
SHOW_DASHBOARD_ALERT_STATS = True


@ui.page('/')
def index_page() -> None:
    """Main dashboard page."""
    camera = get_camera()
    email_system = get_email_system()
    measurement_controller = get_measurement_controller()

    build_header(current_route='/')

    with ui.column().classes('w-full h-full p-4 gap-4 max-w-[1800px] mx-auto'):
        with ui.row().classes('w-full items-stretch gap-4 flex-col xl:flex-row'):
            with ui.column().classes('w-full flex-[1_1_0%] min-w-0'):
                create_camfeed_content()

            with ui.column().classes('w-full xl:w-[420px] xl:min-w-[420px] gap-4'):
                create_motion_status_element(
                    camera=camera,
                    measurement_controller=measurement_controller,
                )
                create_measurement_card(
                    measurement_controller=measurement_controller,
                    camera=camera,
                    email_system=email_system,
                    show_recipients=SHOW_DASHBOARD_ACTIVE_GROUP_SELECTOR,
                    confirm_stop=True,
                )

        if SHOW_DASHBOARD_ALERT_HISTORY or SHOW_DASHBOARD_ALERT_STATS:
            with ui.row().classes('w-full items-stretch gap-4 flex-col xl:flex-row'):
                if SHOW_DASHBOARD_ALERT_HISTORY:
                    with ui.column().classes('w-full xl:flex-[1.15_1_0%] min-w-0'):
                        create_history_card(max_entries=5)
                if SHOW_DASHBOARD_ALERT_STATS:
                    with ui.column().classes('w-full xl:flex-[0.85_1_0%] min-w-0'):
                        create_stats_card()

    build_footer()
