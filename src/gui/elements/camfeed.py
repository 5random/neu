from nicegui import ui
import time
#import cam 

def create_camfeed_content():
    #cam.start()  # Ensure the camera setup is initialized
    with ui.card().style("align-self:stretch; justify-content:center; align-items:start;"):
            ui.label('Camera Feed').classes('text-h6 font-semibold mb-2')
            videoimage = ui.interactive_image('https://picsum.photos/id/377/640/360').classes('w-auto h-full rounded-lg shadow-md')
            #ui.timer(interval=0.1, callback=lambda: videoimage.set_source(f'/video/frame?{time.time()}'))