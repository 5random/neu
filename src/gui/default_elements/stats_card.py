from nicegui import ui
from typing import List, Dict, Any
from datetime import datetime, timedelta
from collections import defaultdict
from src.alert_history import get_history_file, load_history_entries
from src.config import get_logger

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
        # Aggregate events by hour for the last 24 hours
        now = datetime.now()
        start_time = now - timedelta(hours=24)
        
        # Initialize buckets for last 24h
        buckets: Dict[str, int] = defaultdict(int)
        # Pre-fill with 0 to ensure continuous line
        for i in range(24):
            t = start_time + timedelta(hours=i)
            key = t.strftime("%Y-%m-%d %H:00")
            buckets[key] = 0
            
        for entry in data:
            ts_str = entry.get('timestamp')
            if not ts_str:
                continue
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                if ts >= start_time and ts < now:
                    key = ts.strftime("%Y-%m-%d %H:00")
                    buckets[key] += 1
            except ValueError:
                continue
                
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
        ui.label('Network Statistics (Events/Hour)').classes('text-h6')
        
        chart = ui.echart({
            'tooltip': {'trigger': 'axis'},
            'xAxis': {'type': 'category', 'data': []},
            'yAxis': {'type': 'value', 'name': 'Events'},
            'series': [{
                'name': 'Alarms',
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
