<div align="center">

# 🎬 AI 短剧工作室

**从一句大纲，到一条能发出去的竖屏短剧 MP4**

把「AI 写分镜 → 定角色 → 画画面 → 配音 → 让画面动起来 → 合成成片」串成一条流水线的 Web Service

[![CI](https://github.com/rookiecaibai/Created-by-Ai-ai-drama-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/rookiecaibai/Created-by-Ai-ai-drama-studio/actions/workflows/ci.yml)
![tests](https://img.shields.io/badge/tests-222%20passed-brightgreen)
![python](https://img.shields.io/badge/Python-3.12-blue)
![fastapi](https://img.shields.io/badge/FastAPI-0.143-009688)
![react](https://img.shields.io/badge/React-18-61dafb)

</div>

---

## 这是什么

一个完整的 **AI 短剧生产流水线**。你给它一段本集大纲，它会一路做到一段竖屏 MP4：

```
① 一句大纲
      │  大模型
      ▼
② 分镜脚本（景别 / 运镜 / 时长 / 画面描述 / 台词）
      │
      ├──► 角色卡（性别 · 年龄 · 外貌 · 职业 · 背景）
      │         └──► 三视图（正面 / 正侧面 / 背面）
      │                   └──► 裁出「单人正面」当参考图
      │
      ▼
③ 文生图：每个镜头一张画面（768×1344）
      │
      ├──► TTS：逐句情绪 → 语速，按角色分配音色
      ▼
④ 图生视频：每张静图 → 一段会动的 5 秒片段
      ▼
⑤ ffmpeg 合成：片段（或静图）+ 配音 → 竖屏 MP4
```

**不只"能跑"** —— 这个项目里每个非显然的设计都写了"为什么这么做"，以及踩坑时看到的具体报错。
完整理由见 [docs/00-架构决策.md](docs/00-架构决策.md)，零基础全景见 [docs/04-项目全景介绍.md](docs/04-项目全景介绍.md)。

---

## 目录

- [亮点](#亮点)
- [快速开始](#快速开始)
- [配置](#配置)
- [技术栈](#技术栈)
- [架构](#架构)
- [目录结构](#目录结构)
- [测试与 CI](#测试与-ci)
- [文档](#文档)
- [成本与免责](#成本与免责)

---

## 亮点

### 1. 四种模态都可插拔，换厂商不改业务代码

文本 / 图片 / 配音 / 视频各自有一套 `ABC + mock + openai_compat + factory`，
业务层只依赖接口。**不填任何 API Key 也能跑通全流程**（默认走 `mock`，产出假图假音），
所以 clone 下来就能点、能测、能看懂结构。

### 2. BYOK：每部短剧可以用自己的 Key

「短剧设置」里可以给文本 / 图片 / 配音 / 视频分别指定 provider、`base_url`、模型名和自己的 API Key。

| 安全设计 | 做法 |
|---|---|
| Key 落库 | **Fernet 加密**（`enc:v1:` 前缀），不是明文 |
| 接口回显 | **永不返回明文**，只给掩码 `sk-abc…9f2`；且只有"用户自己填的"才显示 |
| `base_url` | **SSRF 校验**：只放行 https，拒绝内网 / 环回 / 链路本地地址 |
| 服务端兜底 | `ALLOW_SERVER_KEY_FALLBACK=false` 可切成**严格 BYOK**（公网部署建议开） |

### 3. 角色一致性：从"文字描述"到"参考图"

先用角色卡生成 **三视图**（正面 / 正侧面 / 背面），再拿它去约束每个镜头。

这里有两个实测出来的关键认知：

- **整张三视图不能直接当参考图** —— 三个视角并排会被模型理解成"三个人"，画出一排一模一样的角色。
  所以要先裁成**单人单视角**（自动检测内容列块，取最左边那个正面），并归一化到安全画布。
- **不是所有厂商都吃参考图** —— `ImageProvider` 上有 `supports_reference` 能力标志，
  不支持的厂商（如智谱 cogview 系列，纯文本输入）自动降级为文字锚定并记日志，换厂商只改一个实现。

### 4. 配音：情绪是有代价的，所以只说能做的

- 台词解析会拆出「谁说的」和「说什么」，**角色名绝不会被念出来**（早期 bug，用语音转文字反证过）
- 每个角色按性别 + 年龄段从音色矩阵取 `音色 / 音高 / 语速`，支持变调
- 逐句让 LLM 判断情绪与强度 → 映射到**语速**
- **诚实边界**：情绪目前只有"语速"一个维度，做不到"急迫"和"愤怒"听起来不一样。
  想真正贴合情绪，需要换支持 instructions 的 TTS 并打开 `TTS_INSTRUCTIONS_PARAM`

### 5. "画面到底动了没有"是可验证的

不做图生视频，成片就是「静图定格 + 配音」的幻灯片。项目对此有**像素级断言**（提取片段首末帧逐像素比对）：

| 状态 | 像素变化 |
|---|---|
| 静态图 | 0.0% |
| 接入动态片段 | 12.4% ~ 64% |

三种模式：`none`（退回静图）/ `mock`（本地 ffmpeg 运镜，**免费、不联网、画面真的会动**）/ `openai_compat`（真调图生视频模型）。

### 6. 异步任务 + 配额 + 调用流水

一张任务表支持**六种任务**（`kind = shots / images / clips / turnaround / audio / video`），
同时支持**集级**和**剧级**两种粒度（角色三视图不属于任何一集）。
`202 + 轮询`，带进度（`total / done`）；服务重启后自动把卡住的"假进行中"任务标记为失败。
配额**按用途分别设限**（分镜 / 大纲 / 画面 / 动态镜头 / 三视图 / 配音 / 成片），并记录调用流水。

---

## 快速开始

### 方式一：Docker（一条命令）

```bash
cd backend                 # ⚠️ 必须在 backend 目录下执行，compose 的相对路径以它为准
docker compose up --build
```

打开 **<http://localhost:8080>**（Nginx 的 8080，不是后端的 8000）。

| 设计点 | 说明 |
|---|---|
| 配置来源 | compose 自动读取 `backend/.env` 做变量替换，**密钥不进 compose 文件** |
| 前端怎么进镜像 | 多阶段构建：node 阶段 `npm run build` → 只把 `dist` 拷进 python 镜像 |
| 媒体存哪 | 挂载宿主机 `backend/media/`，容器重建不丢 |
| 容器里要装 ffmpeg 吗 | **不用**，`imageio-ffmpeg` 自带含 libx264 的静态构建 |
| 公网能访问后端吗 | 不能：api 只 `expose`，8000 不出容器网络，只有 Nginx 对外 |
| 表结构 | 容器入口自动 `alembic upgrade head`（幂等） |

### 方式二：本地跑

```bash
# 0) 在【项目根目录】建虚拟环境（是 ai-drama-studio 这一层，不是 backend/）
python -m venv .venv
.venv\Scripts\activate

# 1) 装依赖
python -m pip install -r backend\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2) 配置（可选 —— 不配也能跑：SQLite + mock 模型）
cd backend
copy .env.example .env

# 3) 启动
python run.py                              # 或 python -m uvicorn app.main:app --reload --port 8000
```

- 界面：**<http://127.0.0.1:8000>**
- 接口文档：**<http://127.0.0.1:8000/docs>**（Swagger UI **本地自托管**，不依赖境外 CDN）

### 前端开发模式

```bash
cd frontend
npm install --registry=https://registry.npmmirror.com
npm run dev      # http://localhost:5173，/api 自动代理到后端 8000
npm run build    # 产物落 frontend/dist，由 FastAPI 托管
```

### 数据库迁移

```bash
cd backend
.venv\Scripts\python.exe -m alembic upgrade head    # 升到最新
.venv\Scripts\python.exe -m alembic check           # 检查模型与库是否一致
```

生产不用 `create_all`（它只建表、不改表）。开发默认 SQLite，生产换 MySQL 只需改 `DATABASE_URL`。

---

## 配置

所有配置走环境变量（`backend/.env`，已被 gitignore 保护）。**全部可留空** —— 留空即 `mock`。

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | 默认 SQLite；生产用 `mysql+pymysql://...?charset=utf8mb4` |
| `JWT_SECRET_KEY` | 登录令牌签名密钥。**同时用于派生加密短剧 Key 的密钥** —— 换掉它，已存的用户 Key 会无法解密（会退化成"没有 Key"，不会 500） |
| `LLM_PROVIDER` / `LLM_MODEL` / `LLM_API_KEY` / `LLM_BASE_URL` | 文本模型（如 DeepSeek） |
| `IMAGE_PROVIDER` / `IMAGE_MODEL` / `IMAGE_API_KEY` / `IMAGE_BASE_URL` / `IMAGE_SIZE` | 文生图，镜头画面尺寸（默认 `768x1344`） |
| `IMAGE_SHEET_SIZE` | **角色三视图**的画布，默认 `1344x768`（横向 —— 三个视角并排，竖屏会把人物压成细条） |
| `WANX_API_KEY` / `WANX_MODEL` / `WANX_BASE_URL` | 万相（阿里云百炼）。`IMAGE_PROVIDER=wanx` 时使用，是**目前唯一支持参考图**的图片渠道 |
| `TTS_PROVIDER` / `TTS_MODEL` / `TTS_VOICE` | 配音（如智谱 GLM-TTS） |
| `TTS_INSTRUCTIONS_PARAM` | 厂商支持"语气参数"时填参数名，语音才真的能带情绪 |
| `VIDEO_CLIP_PROVIDER` | `none` / `mock`（免费本地运镜）/ `openai_compat`（真图生视频） |
| `ALLOW_SERVER_KEY_FALLBACK` | `true`（默认）：用户没填 Key 时用服务端 Key；**公网部署建议 `false`** |
| `DAILY_*_LIMIT` | 各类调用每日配额 |
| `IMAGE_MIN_INTERVAL_SEC` / `IMAGE_RETRY_ATTEMPTS` | 文生图节流与 429 重试（并发打满厂商限流是踩过的坑） |

---

## 技术栈

| 层 | 选型 | 一句话理由 |
|---|---|---|
| Web 框架 | FastAPI | 异步 + 自动 OpenAPI + Pydantic 校验；AI 接口大量是慢 IO |
| 数据校验 | Pydantic v2 | 请求/响应契约显式化，前端不用猜字段 |
| ORM | SQLAlchemy 2.1 | 类型注解式建模，Alembic 生态成熟 |
| 数据库 | SQLite（开发）/ MySQL（生产） | 开发零配置，生产换一个环境变量 |
| 前端 | Vite 5 + React 18 + TypeScript | 无框架负担，构建产物由后端同源托管（没有跨域问题） |
| 部署 | Docker + Nginx | 环境一致 + 反向代理 |
| 测试 | pytest + httpx | 依赖覆盖换测试库，**不联网、不花钱** |

> **为什么接口文档自托管**：FastAPI 默认让 `/docs` 去境外 CDN 拉 Swagger UI 的 JS。
> 该 CDN 在国内常连不上，表现是 **HTML 返回 200、页面却一片空白、服务端日志毫无异常** —— 极难排查。
> 现在静态文件放在 `backend/app/static/`，并有回归测试保证 CDN 不会被重新引入。

---

## 架构

```
浏览器
  │
  ├─ 前端（React）  由 FastAPI 同源托管，或 Nginx 静态分发
  │
  ▼
Nginx ──► FastAPI（路由层很薄）
              │
              ├─ services/         业务逻辑
              │    ├─ script_service      大纲 → 分镜
              │    ├─ cast_service        角色表
              │    ├─ character_look      角色卡 → 画面提示词
              │    ├─ portrait            三视图 → 单人参考图
              │    ├─ emotion_service     逐句情绪 → 语速
              │    ├─ voice_profile       性别/年龄 → 音色矩阵
              │    ├─ tts_service         多角色逐句配音
              │    ├─ clip_service        图生视频
              │    ├─ video_service       ffmpeg 合成
              │    └─ model_config        BYOK 解析 + 密钥加解密
              │
              ├─ llm/ images/ tts/ videos/    四种模态 Provider（各含 mock）
              │
              └─ SQLAlchemy ──► SQLite / MySQL
                                 └─ Alembic 迁移（12 条）
```

**几条贯穿全项目的原则**

1. **用户写的大纲永远优先于模型的建议** —— 生成分镜时只读它、不覆盖它
2. **生成的媒体不进静态目录**，必须走「登录 + 归属校验」的接口读取（静态目录谁猜到 URL 谁就能看）
3. **数据库的级联删除不会删磁盘文件** —— 删除业务数据时要顺手清理，否则一堆孤儿文件
4. **层与层的契约用 Pydantic 显式化**，ORM 模型不直接对外

---

## 目录结构

```
backend/
├── app/
│   ├── main.py              # 应用组装（工厂 + lifespan + 前端托管）
│   ├── core/                # 配置、日志、统一错误、JWT、限流
│   ├── db/                  # ORM 基类、引擎/会话、数据模型
│   ├── schemas/             # 对外契约（Pydantic），与 ORM 分离
│   ├── api/                 # 路由层（薄）+ 依赖注入
│   ├── services/            # 业务逻辑 + 四种模态 Provider 抽象
│   └── static/              # 自托管的 Swagger UI 资源
├── tests/                   # 222 个用例，假模型 + 临时库，不联网
├── alembic/                 # 12 条迁移脚本
├── tools/check_secrets.py   # 密钥泄露扫描（工作区 / 暂存区 / 全历史）
├── Dockerfile               # 多阶段：node 构建前端 → python 运行时
├── docker-compose.yml       # api + MySQL + nginx
└── nginx/nginx.conf         # 反向代理 + 媒体流 + 前端兜底

frontend/                    # Vite + React + TS
└── src/                     # api 客户端、工作区、角色卡、短剧设置、消息灯箱

scripts/hooks/pre-commit     # 提交前拦截疑似密钥（core.hooksPath 安装）
docs/                        # 6 篇文档：架构决策 / 代码导览 / PyCharm / 零基础 / 全景 / 部署
```

---

## 测试与 CI

```bash
cd backend
python -m pytest          # 222 个用例
```

| | |
|---|---|
| 用例数 | **222**（24 个测试文件） |
| 外部依赖 | **零** —— 用依赖覆盖注入假模型 + 临时 SQLite，不联网、不花钱 |
| 覆盖的事故 | 429 限流重试、参考图被拒降级、迁移与模型是否一致、任务重启恢复、越权 404、删集清媒体…… |
| CI | GitHub Actions（`.github/workflows/ci.yml`） |

**提交前还有一道自动化防线**：pre-commit 钩子会扫描暂存区的疑似密钥并阻止提交
（这个仓库是公开的，误提交 = 立刻泄露）：

```powershell
pwsh scripts/install_hooks.ps1                    # 安装一次即可
python backend/tools/check_secrets.py --history   # 想扫全历史随时跑
```

---

## 文档

| 文档 | 内容 |
|---|---|
| [00-架构决策](docs/00-架构决策.md) | 每个选型的理由，以及**没选**的方案为什么没选 |
| [01-代码导览](docs/01-代码导览.md) | 按请求链路走一遍代码，适合接手者 |
| [02-PyCharm 配置](docs/02-PyCharm配置.md) | 解释器、Sources Root、运行配置 |
| [03-从零理解这个项目](docs/03-从零理解这个项目.md) | 零基础向 |
| [04-项目全景介绍](docs/04-项目全景介绍.md) | 全景 + 踩坑合集（最有料的一篇） |
| [05-部署验证清单](docs/05-部署验证清单.md) | Docker 实机验证结果与排查表 |

---

## 成本与免责

**跑真模型是要花钱的**，请先看价格再批量点按钮：

| 动作 | 大致量级 |
|---|---|
| 一集 6 个镜头 · 文生图 | 免费模型 0 元；付费 0.06~0.1 元/张 |
| 一集 6 个镜头 · 图生视频 | 0.5~2.5 元/次 → **一集 3~15 元** |
| 角色三视图 | 每个角色一次图片调用 |
| 配音 | 按字符计，可忽略 |

> **建议顺序**：先只用「生成画面 → 生成配音 → 合成视频」看一版静图片子，
> 确认分镜节奏和画面都满意了，再花钱点「生成动态镜头」。

**内容合规**：AI 生成的人脸、声音和情节由使用者负责。请勿用于伪造他人形象、声音或违法内容。

**许可证**：本仓库暂未声明开源许可证。如需使用请先联系作者。

---

<div align="center">

如果这个项目对你有帮助，欢迎点个 ⭐

</div>
