# AI Drama Studio

一个 AI 短剧创作的 Web Service：管理短剧 → 剧集 → 分镜，并调用大模型生成可拍摄的分镜脚本。

> 这个项目的目标不只是"能跑"，而是**每一层都能讲清为什么这么设计**。
> 架构决策的完整理由见 [docs/00-架构决策.md](docs/00-架构决策.md)。

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
| GET | `/api/v1/episodes/{id}` | 剧集详情（含分镜） |
| POST | `/api/v1/episodes/{id}/generate` | **AI 生成本集剧本与分镜**（幂等覆盖） |

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
├── Dockerfile
├── docker-compose.yml
└── nginx/nginx.conf
```

## 当前状态（诚实清单）

- ✅ 后端接口 + 测试：**已在本机实测通过（24 passed）**
- ✅ JWT 鉴权 + 资源归属：注册/登录/me + 用户只能操作自己的短剧，**已实机验证**
- ✅ Mock 模型全流程：无需 API Key 即可跑通
- ⚠️ Docker / docker-compose / Nginx 配置：已编写，但开发机未装 Docker，**尚未实机验证**
- ⚠️ MySQL 生产路径：本机 MySQL 服务在跑，但缺少凭据，未验证
- ⏳ 待做：Alembic 迁移、调用配额/限流、生成任务异步化、前端页面、大模型直连实测、CI 实跑
