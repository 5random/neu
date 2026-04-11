from nicegui import ui

from src.cam.camera import Camera
from src.config import get_logger
from src.gui.ui_helpers import SECTION_ICONS, create_heading_row

logger = get_logger('gui.camfeed')
_VIDEO_STREAM_SOURCE = '/video_feed'
_DEFAULT_FEED_WIDTH = 1280
_DEFAULT_FEED_HEIGHT = 720
_FEED_MIN_HEIGHT_PX = 240


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

def _resolve_camfeed_dimensions(camera: Camera | None) -> tuple[int, int]:
    if camera is not None:
        try:
            profile = camera.get_configured_capture_profile()
            resolution = profile.get('resolution') or {}
            width = int(resolution.get('width', 0) or 0)
            height = int(resolution.get('height', 0) or 0)
            if width > 0 and height > 0:
                return width, height
        except Exception:
            logger.debug('Could not resolve configured camera feed dimensions', exc_info=True)
    return _DEFAULT_FEED_WIDTH, _DEFAULT_FEED_HEIGHT

def _build_camfeed_surface_style(width: int, height: int) -> str:
    width = max(1, int(width))
    height = max(1, int(height))
    return (
        f'width:100%;height:auto;aspect-ratio:{width}/{height};'
        f'min-height:{_FEED_MIN_HEIGHT_PX}px;'
        'display:block;overflow:hidden;'
        'background:#0f172a;border:1px solid rgba(148, 163, 184, 0.24);'
    )

def _build_camfeed_refresh_script() -> str:
    return """
            <script>
            (function(){
                try {
                    var state = window.__cvdDefaultCamState || {};
                    if (state.retryTimer) {
                        clearTimeout(state.retryTimer);
                        state.retryTimer = null;
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

                    function resolveImage(root) {
                        if (!root) return null;
                        var img = root.querySelector('img');
                        if (img) return img;
                        var qimg = root.querySelector('.q-img__image img');
                        if (qimg) return qimg;
                        return null;
                    }
                    function resolveStatus() {
                        return document.getElementById('cvd-default-cam-status');
                    }
                    function setPhase(phase) {
                        var root = document.getElementById('cvd-default-cam');
                        if (root) {
                            root.dataset.streamState = phase || '';
                        }
                    }
                    function setStatus(message, phase) {
                        var label = resolveStatus();
                        if (!label) return;
                        label.textContent = message || '';
                        label.style.display = message ? '' : 'none';
                        label.dataset.streamState = phase || '';
                    }
                    function clearRetry() {
                        if (state.retryTimer) {
                            clearTimeout(state.retryTimer);
                            state.retryTimer = null;
                        }
                    }
                    function cleanupImageListeners() {
                        if (!state.boundImage) return;
                        if (state.onLoad) {
                            state.boundImage.removeEventListener('load', state.onLoad);
                        }
                        if (state.onError) {
                            state.boundImage.removeEventListener('error', state.onError);
                        }
                        state.boundImage = null;
                        state.onLoad = null;
                        state.onError = null;
                    }
                    function scheduleReconnect() {
                        clearRetry();
                        if (document.visibilityState !== 'visible') return;
                        state.retryTimer = window.setTimeout(function() {
                            state.retryTimer = null;
                            start(true);
                        }, 900);
                    }
                    function bindImage(img) {
                        if (!img || state.boundImage === img) return;
                        cleanupImageListeners();
                        state.boundImage = img;
                        state.onLoad = function() {
                            state.active = true;
                            state.connecting = false;
                            clearRetry();
                            setPhase('ready');
                            setStatus('', 'ready');
                        };
                        state.onError = function() {
                            state.active = false;
                            state.connecting = false;
                            setPhase('reconnecting');
                            setStatus('Reconnecting camera...', 'reconnecting');
                            scheduleReconnect();
                        };
                        img.addEventListener('load', state.onLoad);
                        img.addEventListener('error', state.onError);
                    }

                    function start(force) {
                        var root = document.getElementById('cvd-default-cam');
                        if (!root) return;
                        if (document.visibilityState !== 'visible') return;
                        var img = resolveImage(root);
                        if (!img) return;
                        bindImage(img);

                        var url = '/video_feed';
                        var currentSrc = img.getAttribute('src') || '';
                        if (!force && currentSrc === url) {
                            if (img.complete && img.naturalWidth > 0) {
                                state.active = true;
                                state.connecting = false;
                                clearRetry();
                                setPhase('ready');
                                setStatus('', 'ready');
                            } else {
                                state.active = false;
                                state.connecting = true;
                                setPhase('loading');
                                setStatus('Connecting camera...', 'loading');
                                scheduleReconnect();
                            }
                            return;
                        }

                        state.active = false;
                        state.connecting = true;
                        setPhase('loading');
                        setStatus('Connecting camera...', 'loading');
                        try { img.src = url; } catch(e) {}
                    }
                    function stop() {
                        clearRetry();
                        var root = document.getElementById('cvd-default-cam');
                        var img = resolveImage(root);
                        if (img) {
                            try { img.removeAttribute('src'); } catch(e) {}
                            try { img.src = ''; } catch(e) {}
                        }
                        if (root) {
                            try { root.removeAttribute('src'); } catch(e) {}
                        }
                        state.active = false;
                        state.connecting = false;
                        setPhase('paused');
                        setStatus('Camera paused', 'paused');
                    }

                    state.onVisibilityChange = function() {
                        if (document.visibilityState === 'visible') start(true);
                        else stop();
                    };
                    state.onPageShow = function(e) {
                        if (e && e.persisted) start(true);
                        else if (document.visibilityState === 'visible') start(false);
                    };
                    state.onPageHide = stop;
                    state.onBeforeUnload = stop;

                    document.addEventListener('visibilitychange', state.onVisibilityChange);
                    window.addEventListener('pageshow', state.onPageShow);
                    window.addEventListener('pagehide', state.onPageHide);
                    window.addEventListener('beforeunload', state.onBeforeUnload);

                    window.__cvdDefaultCamState = state;
                    setPhase('loading');
                    setStatus('Connecting camera...', 'loading');
                    start(false);
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

        feed_width, feed_height = _resolve_camfeed_dimensions(camera)
        ui.label('Connecting camera...').classes('text-caption font-medium text-slate-400').props(
            'id=cvd-default-cam-status'
        )
        (
            # interactive_image keeps its own reactive src state; leaving it empty
            # makes JS-only img.src changes invisible and fragile on re-renders.
            ui.interactive_image(_VIDEO_STREAM_SOURCE)
            .classes('w-full rounded-lg')
            .style(_build_camfeed_surface_style(feed_width, feed_height))
            .props('id=cvd-default-cam')
        )
        ui.add_body_html(_build_camfeed_refresh_script())
