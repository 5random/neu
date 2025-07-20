import logging
import time
from pathlib import Path
from src.alert import AlertSystem

class Dummy:
    def __init__(self):
        self.logger = logging.getLogger("dummy")


def test_cleanup_removes_old_jpeg(tmp_path):
    # create three files with slight time gaps
    (tmp_path / "alert_old.jpeg").write_text("old")
    time.sleep(0.01)
    (tmp_path / "alert_new.jpg").write_text("new")
    time.sleep(0.01)
    (tmp_path / "alert_latest.png").write_text("latest")

    dummy = Dummy()
    AlertSystem._cleanup_image(dummy, tmp_path, max_files=2)

    remaining = {p.name for p in tmp_path.iterdir()}
    assert "alert_old.jpeg" not in remaining
    assert "alert_new.jpg" in remaining
    assert "alert_latest.png" in remaining
