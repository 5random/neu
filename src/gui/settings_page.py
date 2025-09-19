from typing import Optional

from nicegui import ui, app

from src.cam.camera import Camera
from src.measurement import MeasurementController
from src.notify import EMailSystem
from src.config import get_global_config, get_logger

from src.gui.settings_elements.camera_settings import create_uvc_content
from src.gui.settings_elements.motion_detection_settings import create_motiondetection_card
from src.gui.settings_elements.measurement_settings import create_measurement_card
from src.gui.settings_elements.email_settings import create_emailcard
from src.gui.settings_elements.camfeed_settings import create_camfeed_content
from src.gui.settings_elements.log_settings import create_log_settings
from src.gui.settings_elements.config_settings import create_config_settings

logger = get_logger("settings_page")


def _get_core_instances() -> tuple[Optional[Camera], Optional[MeasurementController], Optional[EMailSystem]]:
    """Retrieve core instances that the default page initialized.

    Uses app.storage.user as a lightweight registry populated by gui_.create_gui.
    """
    cam: Optional[Camera] = app.storage.user.get('cvd.camera')  # type: ignore[assignment]
    meas: Optional[MeasurementController] = app.storage.user.get('cvd.measurement')  # type: ignore[assignment]
    mail: Optional[EMailSystem] = app.storage.user.get('cvd.email')  # type: ignore[assignment]
    return cam, meas, mail


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

    # Header and footer are provided globally by the main app; do not add them here

    # Left drawer with quick links
    with ui.left_drawer().classes('bg-blue-100 w-64 p-2') as left_drawer:
        ui.label('Quick Links').classes('text-bold pl-2 pt-2')
        # Anchor links to scroll to sections
        ui.link('Camera', '#camera').classes('block px-2 py-1 hover:bg-blue-200 rounded')
        ui.link('Motion Detection', '#motion').classes('block px-2 py-1 hover:bg-blue-200 rounded')
        ui.link('Measurement', '#measurement').classes('block px-2 py-1 hover:bg-blue-200 rounded')
        ui.link('E-Mail', '#email').classes('block px-2 py-1 hover:bg-blue-200 rounded')
        ui.link('Configuration', '#config').classes('block px-2 py-1 hover:bg-blue-200 rounded')
        ui.link('Logs', '#logs').classes('block px-2 py-1 hover:bg-blue-200 rounded')

    # Sticky menu button to toggle the left drawer for navigation
    with ui.page_sticky(position='top-left', x_offset=12, y_offset=12):
        ui.button(on_click=left_drawer.toggle, icon='menu').props('fab color=primary')

    # Optional help/contact button
    with ui.page_sticky(position='bottom-right', x_offset=20, y_offset=20):
        ui.button(on_click=lambda: None, icon='contact_support').props('fab')

    # Main content: stacked sections similar to VS Code settings
    with ui.column().classes('w-full gap-4 p-4'):
        # Camera section: 2-column layout
        with ui.card().classes('w-full').props('flat bordered'):
            ui.html('<a id="camera"></a>')
            ui.label('Camera').classes('text-h6 font-semibold mb-2').props('id=camera')
            with ui.grid(columns=2).classes('w-full gap-4'):
                # Left: Live feed with integrated ROI, and motion detection below
                with ui.column().classes('gap-3'):
                    create_camfeed_content(camera)
                    ui.separator()
                    ui.html('<a id="motion"></a>')
                    ui.label('Motion Detection').classes('text-subtitle1 font-semibold').props('id=motion')
                    create_motiondetection_card(camera)
                # Right: UVC settings (now sliders)
                with ui.column().classes('gap-3'):
                    create_uvc_content(camera)

        with ui.card().classes('w-full').props('flat bordered'):
            ui.html('<a id="measurement"></a>')
            ui.label('Measurement').classes('text-h6 font-semibold mb-2').props('id=measurement')
            create_measurement_card(measurement_controller, camera, email_system)

        with ui.card().classes('w-full').props('flat bordered'):
            ui.html('<a id="email"></a>')
            ui.label('E-Mail Notifications').classes('text-h6 font-semibold mb-2').props('id=email')
            create_emailcard(email_system=email_system)

        with ui.card().classes('w-full').props('flat bordered'):
            ui.html('<a id="config"></a>')
            ui.label('Configuration').classes('text-h6 font-semibold mb-2').props('id=config')
            create_config_settings()

        with ui.card().classes('w-full').props('flat bordered'):
            ui.html('<a id="logs"></a>')
            ui.label('Logs').classes('text-h6 font-semibold mb-2').props('id=logs')
            create_log_settings()

    # No local footer: global footer is used


