import numpy as np
from src.cam.motion import MotionDetector, MotionResult
from src.config import MotionDetectionConfig

def create_detector(roi_enabled=False):
    cfg = MotionDetectionConfig(
        region_of_interest={
            'enabled': roi_enabled,
            'x': 0,
            'y': 0,
            'width': 0,
            'height': 0,
        },
        sensitivity=0.5,
        background_learning_rate=0.1,
        min_contour_area=5,
    )
    return MotionDetector(cfg)

def test_detect_motion_no_roi():
    detector = create_detector(roi_enabled=False)
    frame = np.zeros((100, 100), dtype=np.uint8)
    result1 = detector.detect_motion(frame)
    result2 = detector.detect_motion(frame)
    assert isinstance(result1, MotionResult)
    assert isinstance(result2, MotionResult)
    assert result1.roi_used is False
    assert result2.roi_used is False

