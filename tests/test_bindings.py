import unittest
from unittest.mock import MagicMock
from src.gui.bindings import bind_number_slider

class MockControl:
    def __init__(self, value=0):
        self.value = value
        self.events = {}
        self.client = MagicMock()

    def on(self, event, handler):
        self.events[event] = handler

    def trigger(self, event, args=None):
        if event in self.events:
            self.events[event](args)

    def set_value(self, value):
        self.value = value

    def update(self):
        pass

class MockEvent:
    def __init__(self, value):
        self.value = value

class TestBindings(unittest.TestCase):
    def test_bind_number_slider_sync(self):
        number = MockControl(10)
        slider = MockControl(10)
        
        bind_number_slider(
            number, slider,
            min_value=0, max_value=100
        )
        
        # Test slider update -> number update
        slider.trigger('update:model-value', MockEvent(50))
        self.assertEqual(number.value, 50)
        
        # Test number update -> slider update
        number.trigger('update:model-value', MockEvent(25))
        self.assertEqual(slider.value, 25)

    def test_clamping(self):
        number = MockControl(10)
        slider = MockControl(10)
        
        bind_number_slider(
            number, slider,
            min_value=0, max_value=100
        )
        
        # Test max clamp
        slider.trigger('update:model-value', MockEvent(150))
        self.assertEqual(number.value, 100)
        
        # Test min clamp
        number.trigger('update:model-value', MockEvent(-50))
        self.assertEqual(slider.value, 0)

    def test_as_int(self):
        number = MockControl(10)
        slider = MockControl(10)
        
        bind_number_slider(
            number, slider,
            min_value=0, max_value=100,
            as_int=True
        )
        
        slider.trigger('update:model-value', MockEvent(50.6))
        self.assertEqual(number.value, 51)
        self.assertIsInstance(number.value, int)

    def test_on_change_callback(self):
        number = MockControl(10)
        slider = MockControl(10)
        callback = MagicMock()
        
        bind_number_slider(
            number, slider,
            min_value=0, max_value=100,
            on_change=callback
        )
        
        # Slider change (commit=True)
        slider.trigger('change', MockEvent(50))
        callback.assert_called_with(50.0)
        
        # Number blur (commit=True)
        number.trigger('blur', MockEvent(60))
        callback.assert_called_with(60.0)

if __name__ == '__main__':
    unittest.main()
