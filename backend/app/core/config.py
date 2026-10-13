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

    # ---------- 安全 / 鉴权 ----------
    # 生产环境必须通过环境变量覆盖！这个默认值只用于本地开发。
    # 生成真密钥：python -c "import secrets; print(secrets.token_hex(32))"
    jwt_secret_key: str = "dev-only-secret-change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 默认 24 小时

    # ---------- 配额 / 限流 ----------
    # 每用户每天最多生成次数。AI 调用是钱，必须设上限（<=0 表示不限制）
    daily_generation_limit: int = 20
    # 登录失败限流：窗口期内最多失败次数（防密码爆破）
    login_max_attempts: int = 10
    login_window_sec: int = 600

    # ---------- 大模型 ----------
    # mock: 本地假数据，无需 API Key 即可跑通全流程
    # openai_compat: 任何 OpenAI 兼容接口（DeepSeek / 通义 / Kimi / OpenAI ...）
    llm_provider: str = "mock"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_timeout_sec: float = 60.0

    # ---------- 图片生成（文生图） ----------
    # mock: 本地生成一张把提示词画在画面上的占位图，不联网、不花钱（开发和测试用）
    # openai_compat: 任何兼容 OpenAI 图片接口的服务
    #   智谱 CogView-3-Flash（免费） base_url=https://open.bigmodel.cn/api/paas/v4
    #   硅基流动                  base_url=https://api.siliconflow.cn/v1
    image_provider: str = "mock"
    image_api_key: str = ""
    image_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    image_model: str = "cogview-3-flash"
    image_size: str = "1024x1024"
    # 不同厂商对"尺寸"参数的叫法不同：OpenAI / 智谱用 size，硅基流动用 image_size。
    # 做成配置项，换厂商不用改代码。
    image_size_param: str = "size"
    image_timeout_sec: float = 120.0

    # 生成的图片存哪里（相对 backend 目录）；对外通过带鉴权的接口读取，不做静态目录暴露
    media_root: str = "./media"

    # 每用户每天最多生成多少张图 —— 图片比文本贵得多，必须单独设限
    daily_image_limit: int = 30

    # ---------- 配音（TTS） ----------
    # mock: 本地合成一段可播放的提示音，不联网、不花钱
    # openai_compat: 兼容 OpenAI /audio/speech 的服务
    #   硅基流动 CosyVoice：base_url=https://api.siliconflow.cn/v1
    #                        model=FunAudioLLM/CosyVoice2-0.5B  voice=claire
    #   OpenAI：            base_url=https://api.openai.com/v1  model=gpt-4o-mini-tts
    tts_provider: str = "mock"
    tts_api_key: str = ""
    tts_base_url: str = "https://api.siliconflow.cn/v1"
    tts_model: str = "FunAudioLLM/CosyVoice2-0.5B"
    tts_voice: str = "claire"
    tts_response_format: str = "mp3"
    # 有些厂商支持用 instructions 参数控制语气；不支持的厂商发了会 400，
    # 所以默认留空 = 不发送。填上字段名（如 instructions）即可启用镜头的 audio_prompt。
    tts_instructions_param: str = ""
    tts_timeout_sec: float = 120.0

    # 每用户每天最多生成多少段配音
    daily_audio_limit: int = 60

    # ---------- 视频合成 ----------
    # 留空 = 自动查找：先看系统 PATH，再回退到 imageio-ffmpeg 自带的内置二进制
    # （所以开发机不需要手工安装 ffmpeg；生产 Docker 里可以 apt 装一个并在这里指定）
    ffmpeg_path: str = ""
    video_width: int = 720
    video_height: int = 1280  # 竖屏 9:16 —— 短剧就该是竖着的
    video_fps: int = 25
    video_crf: int = 26  # 越小越清晰、文件越大；26 对草稿预览够用
    video_preset: str = "veryfast"
    daily_video_limit: int = 10

    # 启动时把上次运行遗留的"假进行中"任务标记为失败
    # （见 task_service.mark_interrupted_tasks）。测试里关掉：
    # 测试用临时库，任务状态由用例自己控制。
    cleanup_interrupted_tasks: bool = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回全局唯一配置实例（单例）。"""
    return Settings()
