from nicegui import ui
from nicegui_toolkit import inject_layout_tool

inject_layout_tool()

ui.run(title = 'CVD-Tracker', favicon='https://cvd-tracker.com/favicon.ico', port=8080)