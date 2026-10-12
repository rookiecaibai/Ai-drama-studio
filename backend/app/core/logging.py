"""日志配置。

为什么要自己配而不是到处 print：
- print 没有级别、没有时间戳、线上无法按级别过滤，也没法被日志系统采集；
- 统一格式后，出问题能靠日志还原"哪个请求、哪一步、花了多久"。
"""

import logging
import sys

from app.core.config import get_settings

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def setup_logging() -> None:
    settings = get_settings()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()  # 避免重复添加 handler 导致日志打印两遍
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    # 把 uvicorn 的访问日志也纳入统一格式
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
