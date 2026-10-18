"""短剧设置的 Schema：改名 + 每个模态的模型配置（用户带自己的 Key）。

这里有一条**硬规则贯穿整个文件**：
**读取时永远不返回 api_key 明文**，只返回 `has_key` 和掩码。
密钥只进不出 —— 前端不需要回显它，也不该能拿到它。
"""

from pydantic import BaseModel, ConfigDict, Field


class ModalityOverride(BaseModel):
    """某一个模态（文本/图片/配音/视频）的用户自定义配置。

    全部可选：留空的字段回退到服务端默认。
    `provider` 允许 `""`，表示"用服务端的"。
    """

    provider: str | None = Field(
        default=None,
        description="mock / openai_compat / none（仅视频）。留空 = 用服务端默认",
    )
    base_url: str | None = Field(default=None, description="API 地址，必须 https://")
    model: str | None = Field(default=None, description="模型名，如 deepseek-chat / cogview-3-flash")
    # 只在**写入**时使用；读取响应里永远不出现这个字段（只出现 has_key / api_key_hint）
    api_key: str | None = Field(
        default=None,
        description="你自己的 API Key。传空字符串表示清除已保存的 Key",
    )
    # 模态特有：图片/视频的尺寸、配音的音色、视频是否发送 duration
    size: str | None = Field(default=None, description="图片/视频尺寸，如 1024x1024")
    voice: str | None = Field(default=None, description="配音音色 id")
    send_duration: bool | None = Field(default=None, description="视频是否发送 duration 参数")


class ModalityRead(BaseModel):
    """读出来的配置：**没有 api_key**。"""

    provider: str = ""
    base_url: str = ""
    model: str = ""
    size: str = ""
    voice: str = ""
    send_duration: bool | None = None
    #: 是否已经填了自己的 Key
    has_key: bool = False
    #: 掩码，如 `sk-abc…9f2`。前端只用来显示"填的是哪一把"
    api_key_hint: str = ""
    #: "drama" = 用这个短剧自己的配置；"server" = 回退到服务端默认
    source: str = "server"


class DramaSettingsUpdate(BaseModel):
    """更新短剧设置。

    三个部分都可以独立更新：
    - 短剧信息（名称/题材/梗概/风格）
    - 四个模态的模型配置
    """

    # ── 短剧信息 ──
    title: str | None = Field(default=None, max_length=120)
    genre: str | None = Field(default=None, max_length=40)
    logline: str | None = Field(default=None, max_length=500)
    style: str | None = Field(default=None, max_length=200)

    # ── 模型配置（只更新传进来的那些模态）──
    llm: ModalityOverride | None = None
    image: ModalityOverride | None = None
    tts: ModalityOverride | None = None
    video: ModalityOverride | None = None


class DramaSettingsRead(BaseModel):
    """短剧设置全貌：短剧信息 + 四个模态的配置（含服务端默认值，方便前端展示）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    genre: str
    logline: str | None
    style: str
    # 四个模态的"当前生效配置"（用户填的优先，否则是服务端默认值）
    llm: ModalityRead
    image: ModalityRead
    tts: ModalityRead
    video: ModalityRead
    #: 服务端是否允许"不填 Key 就用默认 Key"。
    #: 前端据此提示用户：false 时每个短剧都必须填自己的 Key
    allow_server_key_fallback: bool = True
