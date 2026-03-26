from nicegui import ui
import time
import sys
from pathlib import Path


from src.cam.camera import Camera
from src.config import get_logger
from src.gui.ui_helpers import SECTION_ICONS, create_heading_row

logger = get_logger('gui.camfeed')

def create_camfeed_content() -> None:
        # Kamera initialisieren
        logger.info("Creating camera feed")
        with ui.card().classes('w-full').style("align-self:stretch; justify-content:center; align-items:start;"):
                # Header with quick link to related settings
                with ui.row().classes('items-center justify-between w-full'):
                    create_heading_row(
                        'Camera Feed',
                        icon=SECTION_ICONS['camera'],
                        title_classes='text-h6 font-semibold mb-2',
                        row_classes='items-center gap-2',
                        icon_classes='text-primary text-xl shrink-0',
                    )
                    ui.button(icon='settings', on_click=lambda: ui.navigate.to('/settings#camera')) \
                        .props('flat round dense').tooltip('Open camera settings')
                # Preserve natural aspect ratio to avoid distortion
                videoimage = (
                        ui.interactive_image()
                        .classes('w-full rounded-lg')
                        .style('height:auto;')
                        .props('id=cvd-default-cam')
                )
                # Set an initial source so the underlying <img> exists immediately
                try:
                    videoimage.set_source(f'/video/frame?{time.time()}')
                except Exception:
                    try:
                        videoimage.source = f'/video/frame?{time.time()}'
                    except Exception:
                        pass

                # Client-side refresh that survives browser back/forward cache (bfcache)
                # Uses a JS interval to update the image src regularly. This avoids relying on
                # server-side timers that may be cancelled during route changes.
                ui.add_body_html(
                        """
                        <script>
                        (function(){
                            try {
                                // Clear previous interval if any (when re-entering the page)
                                if (window.__cvdDefaultCamInt) {
                                    clearInterval(window.__cvdDefaultCamInt);
                                    window.__cvdDefaultCamInt = null;
                                }

                                function updateCam() {
                                    var root = document.getElementById('cvd-default-cam');
                                    if (!root) return;
                                    // Try to find the underlying <img> element rendered by Interactive Image
                                    var img = root.querySelector('img');
                                    if (!img) {
                                        // Also try possible nested structures
                                        var qimg = root.querySelector('.q-img__image img');
                                        if (qimg) img = qimg;
                                    }
                                    if (!img) {
                                        // As a fallback, attempt to set source on root (for SVG/image variants)
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
                                    if (window.__cvdDefaultCamInt) return;
                                    updateCam();
                                    window.__cvdDefaultCamInt = setInterval(updateCam, 200);
                                }
                                function stop() {
                                    if (window.__cvdDefaultCamInt) {
                                        clearInterval(window.__cvdDefaultCamInt);
                                        window.__cvdDefaultCamInt = null;
                                    }
                                }

                                // Start immediately if page is visible and on the correct route
                                if (document.visibilityState === 'visible') start();

                                // Handle tab visibility changes
                                document.addEventListener('visibilitychange', function(){
                                    if (document.visibilityState === 'visible') start();
                                    else stop();
                                });

                                // Restart after back/forward cache restore
                                window.addEventListener('pageshow', function(e){
                                    if (e && e.persisted) start();
                                    else if (document.visibilityState === 'visible') start();
                                });

                                // Clean up when navigating away
                                window.addEventListener('pagehide', stop);
                                window.addEventListener('beforeunload', stop);
                            } catch (e) { /* ignore */ }
                        })();
                        </script>
                        """
                )
