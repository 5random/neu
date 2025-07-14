from src.gui.elements.motion_status_element import create_motion_status_element
from src.cam.motion import MotionResult

class DummyCamera:
    def enable_motion_detection(self, callback):
        self.callback = callback

class DummyMC:
    def __init__(self):
        self.called_with = []
    def on_motion_detected(self, result):
        self.called_with.append(result)


def test_motion_event_forwards_to_measurement_controller():
    camera = DummyCamera()
    mc = DummyMC()
    # this should register callback
    create_motion_status_element(camera, mc)
    dummy_result = MotionResult(True, 1.0, timestamp=123.0)
    camera.callback(None, dummy_result)
    assert mc.called_with == [dummy_result]
