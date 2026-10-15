# AI Drama Studio

[![CI](https://github.com/rookiecaibai/Created-by-Ai-ai-drama-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/rookiecaibai/Created-by-Ai-ai-drama-studio/actions/workflows/ci.yml)

一个 AI 短剧创作的 Web Service：管理短剧 → 剧集 → 分镜，并调用大模型生成可拍摄的分镜脚本。

> 这个项目的目标不只是"能跑"，而是**每一层都能讲清为什么这么设计**。
> 架构决策的完整理由见 [docs/00-架构决策.md](docs/00-架构决策.md)；
> 零基础全景介绍见 [docs/04-项目全景介绍.md](docs/04-项目全景介绍.md)。

## 技术栈

| 层 | 选型 | 一句话理由 |
|---|---|---|
| Web 框架 | FastAPI | 异步 + 自动生成 OpenAPI 文档 + Pydantic 校验，AI 类接口大量是慢 IO，异步收益明显 |
| 数据校验 | Pydantic v2 | 请求/响应契约显式化，前端不用猜字段 |
| ORM | SQLAlchemy 2.0 | 类型注解式建模，迁移（Alembic）生态成熟 |
| 数据库 | SQLite（开发）/ MySQL（生产） | 开发零配置起步，生产换成 MySQL 只需改一个环境变量 |
| 大模型 | Provider 抽象 + Mock | 没有 API Key 也能跑通全流程和测试 |
| 部署 | Docker + Nginx | 环境一致 + 反向代理/HTTPS 终止 |
| 测试 | pytest + httpx | 依赖覆盖换测试库，不依赖外部服务 |
| 接口文档 | Swagger UI / ReDoc（**本地自托管**） | 不依赖境外 CDN，内网和离线环境也能打开 `/docs` |

> **关于接口文档为什么自托管**：FastAPI 默认让 `/docs` 去 `cdn.jsdelivr.net` 加载
> Swagger UI 的 JS。该 CDN 在国内网络下经常连不上，表现是
> **HTML 返回 200、页面却一片空白，且服务端日志毫无异常** —— 非常难排查。
> 现在这两个文件放在 `backend/app/static/` 里由本项目自己提供，
> 并且有回归测试（`test_docs_uses_local_assets`）保证 CDN 不会被重新引入。

## 快速开始

### 在 PyCharm 里（推荐，见 [docs/02-PyCharm配置.md](docs/02-PyCharm配置.md)）

项目自带的虚拟环境 `.venv` 已经建好并装齐依赖，**直接用就行**：

```
File → Open → 选择 ai-drama-studio 根目录（不要选 backend）
右下角解释器 → Add Local Interpreter → Existing → .venv\Scripts\python.exe
右键 backend 文件夹 → Mark Directory as → Sources Root
右键 backend/run.py → Debug
```

### 在命令行里

```bash
# 0) 先在【项目根目录】建虚拟环境 —— 是 ai-drama-studio 这一层，不是 backend/ 里！
#    （虚拟环境放根目录，PyCharm 能自动识别，也符合 .gitignore 的约定）
python -m venv .venv
.venv\Scripts\activate          # 命令行提示符前面出现 (.venv) 就表示激活成功

# 1) 装依赖（国内用镜像；若 pip 卡死且无输出，先执行 python -m pip cache purge 清缓存）
python -m pip install -r backend\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2) 进入后端目录
cd backend

# 3) 配置（可选，不配也能跑：默认 SQLite + mock 模型）
copy .env.example .env

# 4) 启动（--reload 是热重载；调试断点请用 python run.py）
python -m uvicorn app.main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000/docs> 就能看到自动生成的接口文档并直接点着调。

```bash
# 跑测试
python -m pytest
```

## 接口一览

> 除 `/health`、`/docs` 和注册登录外，所有接口都需要请求头
> `Authorization: Bearer <token>`（token 由 `/auth/login` 返回）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查（含数据库连通性，公开） |
| POST | `/api/v1/auth/register` | 注册 |
| POST | `/api/v1/auth/login` | 登录（表单），返回 JWT |
| GET | `/api/v1/auth/me` | 当前登录用户 |
| POST | `/api/v1/dramas` | 创建短剧 |
| GET | `/api/v1/dramas?offset=0&limit=20` | 我的短剧列表（分页，只返回自己的） |
| GET | `/api/v1/dramas/{id}` | 短剧详情（含剧集；别人的剧返回 404） |
| PATCH | `/api/v1/dramas/{id}` | 局部更新 |
| DELETE | `/api/v1/dramas/{id}` | 删除（级联删剧集和镜头） |
| POST | `/api/v1/dramas/{id}/episodes` | 新增一集（集数自动递增） |
| GET | `/api/v1/dramas/{id}/episodes` | 剧集列表 |
| POST | `/api/v1/dramas/{id}/synopsis/generate` | **AI 起草本集大纲**（依据标题 + 前几集内容，只返回建议不写库） |
| GET | `/api/v1/episodes/{id}` | 剧集详情（含分镜） |
| PATCH | `/api/v1/episodes/{id}` | 修改本集标题 / 大纲（用户自己写的内容） |
| POST | `/api/v1/episodes/{id}/generate` | **创建 AI 生成分镜任务**（202，异步执行） |
| POST | `/api/v1/episodes/{id}/images` | **批量生成镜头画面**（文生图，202，异步执行） |
| POST | `/api/v1/episodes/{id}/clips` | **批量生成动态片段**（图生视频，让画面真的动起来，202） |
| POST | `/api/v1/episodes/{id}/audio` | **批量生成台词配音**（TTS，只给有台词的镜头，202） |
| POST | `/api/v1/episodes/{id}/video` | **合成竖屏 MP4**（本地 ffmpeg，202，异步执行） |
| GET | `/api/v1/shots/{id}/image` | 读取镜头画面（**需登录 + 归属校验**，返回 PNG/JPEG 字节） |
| GET | `/api/v1/shots/{id}/clip` | 预览镜头动态片段（需鉴权，返回 MP4） |
| GET | `/api/v1/shots/{id}/audio` | 读取镜头配音（同样需鉴权，Content-Type 按实际格式给） |
| GET | `/api/v1/episodes/{id}/video` | 播放/下载这一集的成片（需鉴权，返回 MP4） |
| GET | `/api/v1/tasks/{id}` | 查询任务状态（轮询这个接口） |
| GET | `/api/v1/tasks?episode_id={id}` | 我的生成任务列表 |

### 创作流程（前端实际的使用顺序）

```
① 建短剧（剧名 / 题材 / 一句话梗概 / 视觉风格）
② ＋ 新增一集 → 弹面板：本集标题 + 本集内容（大纲）
      └─ 「AI 生成大纲」：依据本集标题 + 前面几集的内容起草，可改后再保存
③ 该集下方紧接着「镜头生成」：填镜头数 + 额外要求 → AI 生成分镜
      └─ 202 + 任务号，前端轮询进度，完成后刷新分镜表
④ 点「生成画面」：把每个镜头的英文提示词交给文生图模型 → 分镜表里出现缩略图
⑤ 点「生成动态镜头」：把静图变成真的会动的短片（图生视频）
      └─ 分镜表里的缩略图会变成静音循环播放的小视频，一眼看到"画面在动"
⑥ 点「生成配音」：把每个镜头的台词交给 TTS → 只有台词的镜头会生成，可在表里播放
      └─ 多角色对白会按角色分配不同音色（角色表可由 AI 分析、也可人工修改）
⑦ 点「合成视频」：按镜头顺序，把画面/动态片段 + 配音按时长拼成竖屏 MP4 → 在线播放/下载
```

### 关于第⑤步：为什么必须有它

不做这一步，成片就是「每张图定格 + 配音」的幻灯片 —— **画面完全静止，只有声音在动**。
本项目对"画面到底动了没有"有**像素级的自动化断言**（提取片段首末帧逐像素比对），
实测数据：静态图 0.0% 像素变化，接入动态片段后 12.4%~64% 像素变化。

- `VIDEO_CLIP_PROVIDER=mock`：本地 ffmpeg 运镜（推近/拉远/左右平移），**免费、不联网**，画面确实会动（是运镜，不是人物真做动作）
- `VIDEO_CLIP_PROVIDER=openai_compat`：真调图生视频模型（如智谱 `cogvideox-3`，异步提交 + 轮询）
- 没启用、或某个镜头的片段生成失败时，合成环节会**自动退回静态图**，成片照样能出

**两条重要的设计原则**：

1. **用户写的大纲永远优先于模型的建议** —— 生成分镜时只读它、不会覆盖它
   （`script_service` 里"仅当为空才填"的逻辑）。
2. **生成的图片不进静态目录，必须带鉴权读取** —— 静态目录谁猜到 URL 谁就能看，
   而用户生成的画面是私有数据（见 [shots.py](backend/app/api/v1/shots.py)）。

### 试一遍完整流程

```bash
# 1) 注册 + 登录拿 token
curl -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"pw12345678"}'

curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d 'username=demo&password=pw12345678'
# 把返回里的 access_token 存成 TOKEN

# 2) 建剧
curl -X POST http://127.0.0.1:8000/api/v1/dramas \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"title":"雨夜便利店","genre":"都市","logline":"雨夜的一次重逢"}'

# 3) 加一集（假设剧 id=1）
curl -X POST http://127.0.0.1:8000/api/v1/dramas/1/episodes \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"title":"第1集"}'

# 4) 生成分镜（假设剧集 id=1）
curl -X POST http://127.0.0.1:8000/api/v1/episodes/1/generate \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"shot_count":6}'
```

> 更省事的方式：打开 PyCharm 里的 [api.http](backend/api.http)，按注释顺序点绿色三角即可。

## 数据库迁移（Alembic）

生产环境不用 `create_all`（它只会新建表、不会改表），表结构变更走**迁移脚本**：

```bash
cd backend

# 模型变了之后：自动生成迁移脚本（生成后务必人工 review！）
.venv\Scripts\python.exe -m alembic revision --autogenerate -m "描述这次变更"

# 把库升级到最新
.venv\Scripts\python.exe -m alembic upgrade head

# 检查模型和库是否一致（不一致=忘了生成迁移）
.venv\Scripts\python.exe -m alembic check

# 回滚一步（演练：先 downgrade -1 再 upgrade head）
.venv\Scripts\python.exe -m alembic downgrade -1
```

## 切换到本机 MySQL（已实测通过）

本机 MySQL 9.6 已建好库 `ai_drama`（utf8mb4）和专用账号 `drama`，
迁移和全链路冒烟均已实测。切过去只需要环境变量：

```bash
cd backend
# PowerShell 里设置（或写进 .env，注意 .env 已被 gitignore 保护）
$env:DATABASE_URL = "mysql+pymysql://drama:drama_password_change_me@127.0.0.1:3306/ai_drama?charset=utf8mb4"
$env:AUTO_CREATE_TABLES = "false"     # 表结构由 alembic upgrade head 管理
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

**安全提醒**：你本机 MySQL 的 root 密码目前是 `123456`，太弱了。
如果这台机器连过公网或共享过网络，建议改掉：
`ALTER USER 'root'@'localhost' IDENTIFIED BY '一个强密码';`
（我们的应用只用 `drama` 这个最小权限账号，不受影响。）

## 用 Docker 一键起（api + MySQL + Nginx）

> ⚠️ **这套配置尚未实机验证过**（开发机没装 Docker）。已做过 45 项静态检查，
> 实机步骤和排查表见 [docs/05-部署验证清单.md](docs/05-部署验证清单.md)。

```bash
cd backend                 # 必须在 backend 目录下执行（compose 的相对路径以它为准）
docker compose up --build  # 首次会构建镜像（装 pip/npm 依赖）
```

然后打开 **<http://localhost:8080>**（走的是 Nginx 的 8080，不是后端的 8000）。

**几个关键设计**：

| 点 | 说明 |
|---|---|
| 配置从哪来 | compose 会**自动读取 `backend/.env`** 做变量替换，所以密钥不用写进 compose 文件（那样会跟着 Git 泄露） |
| 前端怎么进镜像 | 多阶段构建：node 阶段 `npm run build` → 只把 `dist` 拷进 python 镜像（node 不会留在成品里） |
| 生成的媒体存哪 | 挂在宿主机的 `backend/media/`，**容器重建不会丢** |
| 容器里要装 ffmpeg 吗 | **不用**：requirements 里的 `imageio-ffmpeg` 自带含 libx264 的静态构建 |
| 谁能从公网访问后端 | 只有 Nginx：api 用 `expose` 而非 `ports`，8000 端口不出容器网络 |
| 表结构怎么建 | 容器入口自动跑 `alembic upgrade head`（幂等），应用侧 `AUTO_CREATE_TABLES=false` |

```bash
docker compose ps                        # 看健康状态
docker compose logs -f api               # 看后端日志
docker compose exec db mysql -udrama -p... ai_drama -e "show tables;"
docker compose down                      # 停止（媒体文件保留）
docker compose down -v                   # 停止并删库（数据全清）
```

---

## 前端（Vite + React + TypeScript）

前端代码在 `frontend/`，构建产物由 FastAPI 直接托管（同源部署，没有跨域问题）。

```bash
cd frontend

# 开发模式：热更新 + 自动把 /api 代理到后端 8000
npm install --registry=https://registry.npmmirror.com
npm run dev          # 打开 http://localhost:5173

# 生产构建：产物落到 frontend/dist，后端启动后由它托管
npm run build
```

构建完重新启动后端，打开 <http://127.0.0.1:8000> 就是完整界面：
注册/登录 → 建短剧 → 加集 → **AI 生成分镜（202 + 轮询进度）** → 查看分镜表。

## 目录结构

```
backend/
├── app/
│   ├── main.py              # 应用组装（工厂函数 + lifespan + 前端托管）
│   ├── core/                # 配置、日志、统一错误、JWT、限流
│   ├── db/                  # ORM 基类、引擎/会话、数据模型
│   ├── schemas/             # 对外契约（Pydantic），与 ORM 模型分离
│   ├── api/                 # 路由层（薄）+ 依赖注入
│   └── services/            # 业务逻辑层
│       ├── dialogue.py      # 台词解析：拆出「谁说的」和「说什么」
│       ├── cast_service.py  # 角色表（AI 分析 + 人工可改）
│       ├── emotion_service.py   # 逐句情绪 → 语速
│       ├── voice_profile.py # 音色矩阵（性别/年龄 → 音色+音高+语速）
│       ├── tts_service.py   # 多角色逐句配音
│       ├── clip_service.py  # 图生视频（逐镜头动态片段）
│       ├── video_service.py # ffmpeg 合成（优先动态片段，退回静图）
│       └── llm/ images/ tts/ videos/   # 四种模态的 Provider 抽象（各含 mock）
├── tests/                   # pytest 163 个用例（用假模型 + 临时库，不联网）
├── alembic/                 # 数据库迁移脚本（versions/ 是版本历史，10 条）
├── Dockerfile               # 多阶段构建：node 构建前端 → python 运行时
├── docker-compose.yml       # api + MySQL + nginx 一键起
└── nginx/nginx.conf         # 反向代理 + 媒体流 + 前端兜底

frontend/                    # Vite + React + TS 前端
├── src/                     # api 客户端、登录页、工作区、AuthMedia、类型定义
└── dist/                    # 构建产物（gitignore，由 FastAPI 托管）

.dockerignore                # ⚠️ 在仓库根：防止 backend/.env 被构建进镜像
```

## 当前状态（诚实清单）

**已实测通过：**

- ✅ 后端接口 + 测试：**163 个用例全绿**，CI 自动跑
- ✅ **用户可控的每集大纲**：自己写 / PATCH 修改 / AI 依据「标题 + 前几集」起草（只给建议不写库）
- ✅ **文生图**：镜头提示词 → 画面，异步任务 + 进度，图片由**带鉴权的接口**提供（真实智谱已出图）
- ✅ **多角色情绪配音**：台词解析（角色名绝不被念出）→ 角色表 → 逐句情绪 → 各自音色/音高/语速合成 → 拼接
  （真实 GLM-TTS 已出真声；情感标签不能塞进文本这点用**语音转文字反证**过）
- ✅ **图生视频**：静图 → 真的会动的短片（真实 CogVideoX-2 已出片，5.1 秒/1080x1920）
  - 三种模式：`none` 退回静图 / `mock` 本地运镜（免费且画面真会动）/ `openai_compat` 真模型
  - **像素级断言**守护"画面真的在动"：静态图 0.0% 像素变化，接入后 45%~64%
- ✅ **视频合成**：优先用动态片段、没有就退回静图，逐段标准化后拼接成竖屏 MP4（720x1280）
- ✅ JWT 鉴权 + 资源归属：用户只能操作自己的短剧与媒体
- ✅ Alembic 迁移：**10 条**迁移链，`alembic check` 零差异，有集成测试守护
- ✅ 调用配额/限流：**按用途分别设限**（分镜/大纲/画面/动态镜头/配音/成片）+ 调用流水 + 登录防爆破
- ✅ 生成任务异步化：一张任务表支持**五种任务**（`kind=shots/images/clips/audio/video`），202 + 轮询 + 进度
- ✅ 服务重启后自动把卡住的"假进行中"任务标记为失败
- ✅ 真实大模型已接通：DeepSeek（文本）+ 智谱（图片/配音/视频）
- ✅ 前端：Vite + React + TS，构建产物由 FastAPI 托管；分镜表可直接循环预览动态片段
- ✅ MySQL 生产路径：本机 MySQL 9.6 实测通过（utf8mb4 生效）

**还没做 / 需要注意：**

- ✅ **Docker / Nginx 整链路已实机验证**（2026-10-15）：三个容器全部 healthy，
  `/`、`/api/v1/health`、`/docs` 全部 200，10 条迁移在容器内 MySQL 上执行成功，
  **媒体持久化**和迁移幂等都已验证，镜像 441MB（多阶段构建生效）
- ⚠️ **角色一致性**：同一角色在不同镜头里长相会变（图生视频通病，需要固定角色参考图）
- ⚠️ 任务队列用的是 FastAPI `BackgroundTasks`（同进程内存），生产应换 Celery/RQ + Redis
- ⚠️ 视频接口不支持 Range 请求，播放器拖不动进度条
- ⚠️ 容器内**没跑过真实视频合成**（合成逻辑在 CI 的 Linux 上测过，但没在容器里实际跑，
  因为要花真钱）；也还没上传到云服务器配 HTTPS 域名
