from nicegui import ui

from src.config import get_logger
from src.gui.default_elements.camfeed import create_camfeed_content
from src.gui.default_elements.measurementcard import create_measurement_card
from src.gui.default_elements.motion_status_element import create_motion_status_element
from src.gui.instances import get_camera, get_email_system, get_measurement_controller
from src.gui.layout import build_footer, build_header

logger = get_logger('gui.index')


@ui.page('/')
def index_page() -> None:
    """Main dashboard page."""
    camera = get_camera()
    email_system = get_email_system()
    measurement_controller = get_measurement_controller()

    build_header()

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
                    show_recipients=False,
                    confirm_stop=True,
                )
                

    build_footer()
