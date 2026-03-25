from nicegui import ui
from typing import List, Dict, Any
from datetime import datetime, timedelta
from collections import defaultdict
from src.alert_history import get_history_file, load_history_entries, parse_history_timestamp
from src.config import get_logger
from src.gui.settings_elements.ui_helpers import SECTION_ICONS, create_heading_row

logger = get_logger('gui.stats')

def create_stats_card() -> None:
    """Creates a card displaying statistics charts."""
    
    history_file = get_history_file()
    
    def load_history() -> List[Dict[str, Any]]:
        try:
            return load_history_entries(history_file=history_file, entry_type='alert')
        except Exception as e:
            logger.error(f"Error loading history for stats: {e}")
            return []

    def process_data(data: List[Dict[str, Any]]) -> Dict[str, Any]:
        # Aggregate events by hour for the last 24 hours, aligned to hour boundaries.
        now = datetime.now()
        end_hour = now.replace(minute=0, second=0, microsecond=0)
        start_hour = end_hour - timedelta(hours=23)
        
        # Initialize buckets for last 24h
        buckets: Dict[str, int] = defaultdict(int)
        for i in range(24):
            t = start_hour + timedelta(hours=i)
            key = t.strftime("%Y-%m-%d %H:00")
            buckets[key] = 0
            
        for entry in data:
            ts = parse_history_timestamp(entry.get('timestamp'))
            if ts is None:
                continue

            if start_hour <= ts <= now:
                key = ts.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:00")
                if key in buckets:
                    buckets[key] += 1
                 
        # Sort by time
        sorted_keys = sorted(buckets.keys())
        values = [buckets[k] for k in sorted_keys]
        # Format labels to be shorter (e.g. "14:00")
        labels = [k.split(' ')[1] for k in sorted_keys]
        
        return {
            'categories': labels,
            'data': values
        }

    with ui.card().classes('w-full h-full'):
        create_heading_row(
            'Alert Statistics (Events/Hour)',
            icon=SECTION_ICONS['stats'],
            title_classes='text-h6',
            row_classes='items-center gap-2',
            icon_classes='text-primary text-xl shrink-0',
        )
        
        chart = ui.echart({
            'tooltip': {'trigger': 'axis'},
            'xAxis': {'type': 'category', 'data': []},
            'yAxis': {'type': 'value', 'name': 'Events'},
            'series': [{
                'name': 'Alerts',
                'type': 'line',
                'data': [],
                'smooth': True,
                'showSymbol': False,
                'areaStyle': {
                    'color': '#19bfd2',
                    'opacity': 0.3
                },
                'lineStyle': {
                    'color': '#19bfd2'
                }
            }],
            'backgroundColor': 'transparent',
        }).classes('w-full h-64')

        def refresh_chart() -> None:
            data = load_history()
            processed = process_data(data)
            chart.options['xAxis']['data'] = processed['categories']
            chart.options['series'][0]['data'] = processed['data']
            chart.update()

        ui.timer(5.0, refresh_chart) # Auto-refresh every 5s
        refresh_chart() # Initial load
