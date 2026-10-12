"""配置中心。

设计要点（为什么这么做，详见 docs/00-架构决策.md）：
1. 所有可变参数集中在 Settings 里，代码里不出现硬编码的端口/密钥/连接串。
2. 用环境变量注入 —— 这是 12-Factor App 的做法，也是 Docker 部署的前提：
   同一个镜像，靠环境变量就能跑在开发机和生产服务器上。
3. get_settings() 用 lru_cache 包一层，保证全局只有一个配置实例（Settings 的解析有开销）。
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_file: 本地开发读 .env；生产环境不挂 .env，直接用容器环境变量覆盖
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # 环境里有无关变量时不报错
    )

    # ---------- 应用 ----------
    app_name: str = "AI Drama Studio"
    api_prefix: str = "/api/v1"
    env: str = "dev"  # dev / test / prod
    debug: bool = True
    log_level: str = "INFO"

    # ---------- 数据库 ----------
    # 本地默认 SQLite（零配置起步）；生产用环境变量换成 mysql+pymysql://...
    database_url: str = "sqlite+pysqlite:///./ai_drama.db"
    # 生产环境应设为 false，改用 Alembic 迁移管理表结构（见 docs/00）
    auto_create_tables: bool = True

    # ---------- 大模型 ----------
    # mock: 本地假数据，无需 API Key 即可跑通全流程
    # openai_compat: 任何 OpenAI 兼容接口（DeepSeek / 通义 / Kimi / OpenAI ...）
    llm_provider: str = "mock"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_timeout_sec: float = 60.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回全局唯一配置实例（单例）。"""
    return Settings()
