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
| GET | `/api/v1/shots/{id}/image` | 读取镜头画面（**需登录 + 归属校验**，返回 PNG 字节） |
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
```

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
│   ├── main.py              # 应用组装（工厂函数 + lifespan）
│   ├── core/                # 配置、日志、统一错误
│   ├── db/                  # ORM 基类、引擎/会话、数据模型
│   ├── schemas/             # 对外契约（Pydantic），与 ORM 模型分离
│   ├── api/                 # 路由层（薄）+ 依赖注入
│   └── services/            # 业务逻辑 + 大模型 Provider 抽象
├── tests/                   # pytest（用假模型 + 临时库，不联网）
├── alembic/                 # 数据库迁移脚本（versions/ 是版本历史）
├── Dockerfile
├── docker-compose.yml
└── nginx/nginx.conf

frontend/                    # Vite + React + TS 前端
├── src/                     # api 客户端、登录页、工作区、类型定义
└── dist/                    # 构建产物（gitignore，由 FastAPI 托管）
```

## 当前状态（诚实清单）

- ✅ 后端接口 + 测试：**已在本机实测通过（49 passed）**
- ✅ **用户可控的每集大纲**：自己写 / PATCH 修改 / AI 依据「标题 + 前几集」起草（只给建议不写库），分镜严格基于该大纲生成
- ✅ **文生图**：把每个镜头的英文提示词变成画面，异步任务 + 进度（3/6），图片存本地并由**带鉴权的接口**提供，删短剧时顺手清理图片文件
- ✅ JWT 鉴权 + 资源归属：注册/登录/me + 用户只能操作自己的短剧与画面，**已实机验证**
- ✅ Alembic 迁移：6 条迁移链，`alembic check` 零差异，有集成测试守护
- ✅ 调用配额/限流：**按用途分别设限**（分镜/大纲/画面）+ 调用流水（带 `kind`）+ 登录防爆破限流
- ✅ 生成任务异步化：一张任务表支持多种任务（`kind=shots/images`），202 + 轮询 + 进度
- ✅ **真实大模型已接通**：DeepSeek（`LLM_PROVIDER=openai_compat`），实测大纲 4 秒、分镜 8 秒
- ✅ 前端：Vite + React + TS，构建产物由 FastAPI 托管（实机验证）
- ✅ MySQL 生产路径：本机 MySQL 9.6 实测通过（账号已收紧到仅本机登录）
- ✅ 已推送 GitHub 并跑通 CI
- ⚠️ 文生图真实厂商未实测：当前 `IMAGE_PROVIDER=mock`（占位图），接真实 Key 后即可出真图
- ⚠️ Docker / docker-compose / Nginx：配置已就绪，整链路仍需装 Docker 后验证
- ⏳ 待做：TTS 配音、视频合成（图+配音按时长拼接）
