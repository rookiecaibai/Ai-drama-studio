"""登录防爆破限流（进程内存版，滑动窗口）。

为什么单独给登录接口做限流：
登录是"猜密码"的入口。没有限流，攻击者可以拿字典穷举，
一天试几十万次，撞库成本几乎为零。
对失败尝试设一个"滑动窗口"上限，是最小成本的对策。

滑动窗口是什么：
不用"整点重置"（0点-24点算一天）那种固定窗口，而是"最近 N 秒内
失败了几次"。固定窗口有个漏洞：攻击者在 23:59:59 和 00:00:01
连续爆破，各占一半额度，等于窗口上限翻倍。

诚实说明本实现的局限（面试会问）：
状态存在进程内存里 —— 服务重启就清零，多实例部署时各算各的。
生产环境应换成 Redis 之类的共享存储，让所有实例共用同一份计数。
"""

import threading
import time
from collections import defaultdict, deque

from app.core.errors import AppError


class LoginLimiter:
    def __init__(self, max_attempts: int, window_sec: int):
        self.max_attempts = max_attempts
        self.window_sec = window_sec
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()  # 登录路由是同步的，会在线程池里并发执行，必须加锁

    def _prune(self, key: str, now: float) -> None:
        """把窗口外的旧记录清掉，避免内存无限增长。"""
        queue = self._failures[key]
        while queue and now - queue[0] > self.window_sec:
            queue.popleft()
        if not queue:
            self._failures.pop(key, None)

    def is_blocked(self, key: str) -> bool:
        if self.max_attempts <= 0:
            return False
        with self._lock:
            self._prune(key, time.monotonic())
            return len(self._failures.get(key, ())) >= self.max_attempts

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(key, now)
            self._failures[key].append(now)

    def clear(self, key: str) -> None:
        """登录成功后清零 —— 不能让正常用户被自己偶尔的手滑锁死。"""
        with self._lock:
            self._failures.pop(key, None)
