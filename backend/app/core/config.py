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

    # 用户没填自己的 API Key 时，是否允许回退到服务端的 Key。
    #   true （默认）：方便试玩 —— 不填也能生成，但**花的是你的钱**
    #   false        ：严格 BYOK —— 每个短剧必须填自己的 Key 才能生成
    # 上线到公网时建议设为 false，否则任何人都能刷爆你的账户。
    allow_server_key_fallback: bool = True
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
    # 尺寸要和成片方向一致！我们的成片是 720x1280 竖屏（9:16），
    # 如果这里生成正方形图，合成时会上下各留一大条黑边（近一半画面是黑的）。
    # 智谱支持的枚举里 768x1344 最接近 9:16，几乎铺满竖屏。
    # （720x1440 是 1:2，更瘦，反而会左右留边 —— 所以选 768x1344。）
    image_size: str = "768x1344"
    # 不同厂商对"尺寸"参数的叫法不同：OpenAI / 智谱用 size，硅基流动用 image_size。
    # 做成配置项，换厂商不用改代码。
    image_size_param: str = "size"
    image_timeout_sec: float = 120.0

    # 生成的图片存哪里（相对 backend 目录）；对外通过带鉴权的接口读取，不做静态目录暴露
    media_root: str = "./media"

    # 厂商私有参数的透传口（JSON 字符串），会合并进请求体。
    # 为什么需要它：不同厂商有自己的私有参数，做成配置就不必为每家改代码。
    # 例（智谱去水印，需先在控制台"个人中心-安全管理-去水印管理"打开开关）：
    #   IMAGE_EXTRA_PARAMS={"watermark_enabled": false}
    # 注意：model / prompt / size 这些必需字段优先级更高，额外参数覆盖不了它们。
    image_extra_params: str = ""
    tts_extra_params: str = ""

    # 每用户每天最多生成多少张图 —— 图片比文本贵得多，必须单独设限
    daily_image_limit: int = 30

    # ---------- 配音（TTS） ----------
    # mock: 本地合成一段可播放的提示音，不联网、不花钱
    # openai_compat: 兼容 OpenAI /audio/speech 的服务
    #   智谱 GLM-TTS（推荐，同一个智谱 Key 就能用）：
    #       TTS_BASE_URL=https://open.bigmodel.cn/api/paas/v4
    #       TTS_MODEL=glm-tts      TTS_VOICE=tongtong
    #   硅基流动 CosyVoice：base_url=https://api.siliconflow.cn/v1
    tts_provider: str = "mock"
    tts_api_key: str = ""
    tts_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    tts_model: str = "glm-tts"
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

    # ---------- 图生视频（让画面真的动起来） ----------
    # 背景：不做这一步，成片就是"每张图定格 + 配音"的幻灯片式拼接 —— 画面完全静止。
    # none:          不生成动态片段（成片仍用静态图，即原来的行为，向后兼容）
    # mock:          本地 ffmpeg 做 Ken Burns 式缓慢推拉摇移 —— 免费、不联网，
    #                画面**确实会动**（是运镜不是真动作），用来跑通链路和演示
    # openai_compat: 真调图生视频模型（智谱 cogvideox-3 等：异步提交 + 轮询取结果）
    video_clip_provider: str = "none"
    # 留空则复用 IMAGE_API_KEY —— 智谱的文生图和图生视频是同一个 Key
    video_api_key: str = ""
    video_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    video_model: str = "cogvideox-3"
    # 竖屏尺寸要和成片一致，否则合成时要补黑边。
    # 注意各模型支持的枚举不同：cogvideox-3 支持 720x1280；cogvideox-flash 只支持 960x1280
    video_size: str = "720x1280"
    video_quality: str = "speed"  # speed=速度优先 / quality=质量优先
    video_clip_seconds: int = 5  # 每个镜头生成的动态片段时长
    # 是否把 duration 发给厂商：**只有 cogvideox-3 支持这个参数**，
    # cogvideox-2 / cogvideox-flash 没有它（发了可能报 400）。
    # 做成开关而不是按模型名硬判断：厂商的枚举随时会变，使用者按当前文档决定更可靠。
    video_send_duration: bool = True
    video_timeout_sec: float = 60.0  # 单次 HTTP 请求超时
    video_poll_interval_sec: float = 6.0  # 轮询异步结果的间隔
    video_poll_timeout_sec: float = 360.0  # 单个片段最长等多久（5 秒的片段不该等超过 6 分钟）

    # 每用户每天最多生成多少个动态镜头（视频模型比图片贵得多，单独设限）
    daily_clip_limit: int = 30

    # 每用户每天最多生成多少张角色三视图。
    # 单独设限的理由：三视图是"一张图 = 一次调用"，而且用户会反复调角色卡重生成，
    # 和"整集批量出图"是两种完全不同的使用节奏，混在一个额度里会互相挤占。
    daily_turnaround_limit: int = 20

    # 文生图的节流与重试（都是被 429 打出来之后加的）：
    # 实测：同时点「生成三视图」和「生成画面」会把免费模型的账户级限流打满，
    # 一次批量 4 张全部 429。所以两次请求之间至少隔一会儿，失败则指数退避重试。
    image_min_interval_sec: float = 1.2
    image_retry_attempts: int = 3

    # ── 万相（阿里云百炼）──
    # 为什么单独一套配置：它是目前唯一「支持参考图」的图像渠道。
    # 智谱的三个画图模型都是纯文本输入，角色只能靠文字描述，长相会飘。
    wanx_api_key: str = ""
    wanx_base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    wanx_model: str = "wan2.7-image"

    # 角色三视图的画布尺寸：**横向**，因为三视图是"三个全身视角从左到右并排"。
    # 用镜头的竖屏 768x1344 去画，三个人会被压成三条细条，看不清形体。
    # 镜头画面继续用 IMAGE_SIZE（竖屏，要贴合成片）。
    image_sheet_size: str = "1344x768"

    # 视频厂商的私有参数透传（JSON 字符串），格式见 IMAGE_EXTRA_PARAMS 的说明。
    # 例：VIDEO_EXTRA_PARAMS={"watermark_enabled": false}
    video_extra_params: str = ""

    # 启动时把上次运行遗留的"假进行中"任务标记为失败
    # （见 task_service.mark_interrupted_tasks）。测试里关掉：
    # 测试用临时库，任务状态由用例自己控制。
    cleanup_interrupted_tasks: bool = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回全局唯一配置实例（单例）。"""
    return Settings()
