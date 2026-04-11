import time

from nicegui import ui

from src.cam.camera import Camera
from src.config import get_logger
from src.gui.ui_helpers import SECTION_ICONS, create_heading_row

logger = get_logger('gui.camfeed')


def _render_camfeed_placeholder() -> None:
    with ui.column().classes('w-full gap-3 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-6 text-slate-700'):
        ui.icon('videocam_off').classes('text-4xl text-slate-500')
        ui.label('Camera not available').classes('text-h6 font-semibold')
        ui.label(
            'The dashboard started in degraded mode. Check the camera settings or reconnect the device to restore the live feed.'
        ).classes('text-body2')
        ui.button('Open camera settings', icon='settings', on_click=lambda: ui.navigate.to('/settings#camera')).props(
            'outline'
        )


def _video_frame_url() -> str:
    return f'/video/frame?{time.time()}'


def _build_camfeed_refresh_script() -> str:
    return """
            <script>
            (function(){
                try {
                    var state = window.__cvdDefaultCamState || {};

                    if (state.intervalId) {
                        clearInterval(state.intervalId);
                        state.intervalId = null;
                    }
                    if (state.onVisibilityChange) {
                        document.removeEventListener('visibilitychange', state.onVisibilityChange);
                    }
                    if (state.onPageShow) {
                        window.removeEventListener('pageshow', state.onPageShow);
                    }
                    if (state.onPageHide) {
                        window.removeEventListener('pagehide', state.onPageHide);
                    }
                    if (state.onBeforeUnload) {
                        window.removeEventListener('beforeunload', state.onBeforeUnload);
                    }

                    function updateCam() {
                        var root = document.getElementById('cvd-default-cam');
                        if (!root) return;
                        var img = root.querySelector('img');
                        if (!img) {
                            var qimg = root.querySelector('.q-img__image img');
                            if (qimg) img = qimg;
                        }
                        if (!img) {
                            var url = '/video/frame?' + Date.now();
                            try { root.src = url; } catch(e) {}
                            try { root.setAttribute('src', url); } catch(e) {}
                            try { root.setAttribute('href', url); } catch(e) {}
                            return;
                        }
                        var url = '/video/frame?' + Date.now();
                        img.src = url;
                    }

                    function start() {
                        if (state.intervalId || document.visibilityState !== 'visible') return;
                        if (!document.getElementById('cvd-default-cam')) return;
                        updateCam();
                        state.intervalId = setInterval(updateCam, 200);
                    }
                    function stop() {
                        if (state.intervalId) {
                            clearInterval(state.intervalId);
                            state.intervalId = null;
                        }
                    }

                    state.onVisibilityChange = function() {
                        if (document.visibilityState === 'visible') start();
                        else stop();
                    };
                    state.onPageShow = function(e) {
                        if (e && e.persisted) start();
                        else if (document.visibilityState === 'visible') start();
                    };
                    state.onPageHide = stop;
                    state.onBeforeUnload = stop;

                    document.addEventListener('visibilitychange', state.onVisibilityChange);
                    window.addEventListener('pageshow', state.onPageShow);
                    window.addEventListener('pagehide', state.onPageHide);
                    window.addEventListener('beforeunload', state.onBeforeUnload);

                    window.__cvdDefaultCamState = state;
                    start();
                } catch (e) { /* ignore */ }
            })();
            </script>
            """


def create_camfeed_content(camera: Camera | None = None, *, camera_available: bool | None = None) -> None:
    logger.info("Creating camera feed")
    resolved_camera_available = bool(camera_available) if camera_available is not None else camera is not None
    with ui.card().classes('w-full').style("align-self:stretch; justify-content:center; align-items:start;"):
        with ui.row().classes('items-center justify-between w-full'):
            create_heading_row(
                'Camera Feed',
                icon=SECTION_ICONS['camera'],
                title_classes='text-h6 font-semibold mb-2',
                row_classes='items-center gap-2',
                icon_classes='text-primary text-xl shrink-0',
            )
            ui.button(icon='settings', on_click=lambda: ui.navigate.to('/settings#camera')).props(
                'flat round dense'
            ).tooltip('Open camera settings')

        if not resolved_camera_available:
            _render_camfeed_placeholder()
            return

        videoimage = (
            ui.interactive_image()
            .classes('w-full rounded-lg')
            .style('height:auto;')
            .props('id=cvd-default-cam')
        )
        source = _video_frame_url()
        try:
            videoimage.set_source(source)
        except Exception as exc:
            logger.debug("interactive_image.set_source failed, falling back to direct source assignment: %s", exc)
            try:
                videoimage.source = source
            except Exception as fallback_exc:
                logger.warning("Failed to set dashboard camera source: %s", fallback_exc)

        ui.add_body_html(_build_camfeed_refresh_script())
