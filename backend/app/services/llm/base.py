"""大模型调用的抽象接口。

为什么要抽这一层（本项目的关键设计）：
1. 业务代码只依赖 LLMProvider 这个"接口"，不关心背后是谁家的模型；
2. 因此可以在没有 API Key、没有网络的情况下用 MockProvider 跑通全部流程和测试 ——
   这是"代码可测试"的前提，也是让 CI 不依赖外部服务的常用手段；
3. 换模型（DeepSeek -> 通义 -> 本地 Ollama）只改配置，不改业务代码。
   这就是依赖倒置（DIP）的实际收益，面试时是个很好的例子。
"""

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def complete(self, *, system: str, user: str) -> str:
        """给定 system/user 提示词，返回模型的纯文本输出。"""
        raise NotImplementedError
