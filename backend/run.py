"""本地开发启动入口 —— 专门为了在 PyCharm 里「一键 Run / Debug」。

为什么不直接用命令行 uvicorn：
- PyCharm 的断点调试需要一个"由它启动的 Python 进程"，uvicorn 命令行启动的进程
  默认挂不上调试器；用这个文件启动，右键 Debug 就能在任意一行代码打断点。

关于热重载（reload）的重要细节：
- reload=True 时 uvicorn 会 fork 出一个子进程来跑应用，断点会失效；
- 所以这里默认关闭，需要热重载时用环境变量开启，或者直接在 PyCharm 终端里
  跑 `python -m uvicorn app.main:app --reload`。

用法：
  1) 在 PyCharm 里右键本文件 -> Run 'run'        （正常启动）
  2) 在 PyCharm 里右键本文件 -> Debug 'run'      （带断点调试，推荐）
  3) 想开热重载：Run/Debug Configurations 里把环境变量 UVICORN_RELOAD 设为 1
"""

import os

import uvicorn

from app.core.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    reload_enabled = os.getenv("UVICORN_RELOAD", "0") == "1"

    print(f"启动 {settings.app_name} | env={settings.env} | llm={settings.llm_provider}")
    print(f"接口文档: http://127.0.0.1:8000/docs   热重载: {'开' if reload_enabled else '关'}")

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=reload_enabled,
        # 开发时把日志级别调成 info，能直接看到每个请求的进出
        log_level="info",
    )
