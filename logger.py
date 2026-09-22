# -*- coding: utf-8 -*-
"""结构化日志：控制台人类可读，文件按时间戳落盘。"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"pipeline_{datetime.now():%Y%m%d_%H%M%S}.log"

    logger = logging.getLogger("painter")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    logger.addHandler(console)
    logger.addHandler(file_handler)
    logger.info(f"日志文件: {log_file}")
    return logger
