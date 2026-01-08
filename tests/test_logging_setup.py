import logging
from src import config as config_module
from src.config import LoggingConfig


from typing import Any

def test_setup_logger_runs_once(tmp_path: Any, monkeypatch: Any) -> None:
    config_module._configured_loggers.clear()
    log_file = tmp_path / "app.log"
    cfg = LoggingConfig(level="INFO", file=str(log_file))
    logger = cfg.setup_logger("test_logger")
    first_count = len(logger.handlers)
    cfg.setup_logger("test_logger")
    second_count = len(logger.handlers)
    assert second_count == first_count
