"""迭代预算 — 线程安全的轮次计数器，支持退款机制。[H]"""

import threading


class IterationBudget:
    """限制 agent 的最大工具调用轮次，防止无限循环。"""

    def __init__(self, max_iterations: int = 60):
        self.max_iterations = max_iterations
        self.consumed = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        """消耗 1 轮预算。返回 True 表示还有余量。"""
        with self._lock:
            if self.consumed >= self.max_iterations:
                return False
            self.consumed += 1
            return True

    def refund(self, amount: int = 1):
        """退款 — 某些操作（如只读查询）不消耗预算。"""
        with self._lock:
            self.consumed = max(0, self.consumed - amount)

    @property
    def remaining(self) -> int:
        return max(0, self.max_iterations - self.consumed)

    @property
    def exhausted(self) -> bool:
        return self.consumed >= self.max_iterations

    def reset(self):
        with self._lock:
            self.consumed = 0
