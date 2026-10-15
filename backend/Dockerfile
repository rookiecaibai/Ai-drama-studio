# ---------- 第 1 阶段：构建前端 ----------
#
# 为什么把前端构建放进后端镜像：
# 后端自己会托管前端产物（见 app/main.py 的 _register_frontend），
# 所以一个容器就能同时提供界面和接口 —— 部署面更小，也不用给 Nginx 挂静态文件卷。
#
# 这就是"多阶段构建（multi-stage build）"：构建阶段的 node 环境**不会**进入最终镜像，
# 最终镜像里只有构建出来的 dist 文件（几十 KB），不会因为装过 node 就胖几百 MB。
FROM node:20-alpine AS frontend

# 国内构建走镜像源快很多；想换回官方源就 --build-arg NPM_REGISTRY=https://registry.npmjs.org
ARG NPM_REGISTRY=https://registry.npmmirror.com

WORKDIR /build

# 先只拷贝依赖清单再安装：只要 package.json / package-lock.json 没变，
# 这一层就能被 Docker 缓存命中，改前端代码后重建只需几秒。
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --registry=${NPM_REGISTRY}

COPY frontend/ ./
RUN npm run build


# ---------- 第 2 阶段：后端运行时 ----------
FROM python:3.12-slim

# 允许构建时切换 pip 源
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# PYTHONDONTWRITEBYTECODE：不生成 __pycache__，镜像更小
# PYTHONUNBUFFERED：日志不缓冲，容器里立刻可见（不然 docker logs 会"卡住"）
# PIP_DISABLE_PIP_VERSION_CHECK：安装时不联网检查 pip 版本
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ⚠️ 目录布局必须和仓库保持一致，这不是随便选的：
# app/main.py 用 `Path(__file__).resolve().parents[2] / "frontend" / "dist"` 找前端产物。
# 如果后端放在 /app，parents[2] 会变成根目录 /，于是它去找 /frontend/dist —— 永远找不到，
# **容器里的 Web 界面直接 404**（API 还是好的，所以很容易误判成"前端没部署"）。
# 按 <root>/backend + <root>/frontend/dist 布局后，路径计算和本地开发完全一致。
WORKDIR /srv/app/backend

# 先只拷贝依赖清单再安装：requirements.txt 没变时这一层缓存命中
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt

COPY backend/ .
# Windows 上创建的文件没有可执行权限，必须在镜像里显式补上
RUN chmod +x docker-entrypoint.sh

# 前端构建产物放进后端约定的位置
COPY --from=frontend /build/dist /srv/app/frontend/dist

# ---------- 关于 ffmpeg（视频合成/音频处理要用）----------
# **不需要** apt-get install ffmpeg。requirements 里的 imageio-ffmpeg 自带一份
# 静态构建（含 libx264 编码器），ffmpeg.py 的三级查找会自动找到它。
# 证据：我们的视频合成测试就是在 CI 的 Linux 上、用这份内置二进制跑通的。
# 代价是镜像小约 300MB。
# 如果将来需要系统 ffmpeg（硬件加速、更多滤镜），在这里 apt-get install ffmpeg
# 并设置环境变量 FFMPEG_PATH=/usr/bin/ffmpeg 即可，代码不用改。

# 容器里必须监听 0.0.0.0，监听 127.0.0.1 的话容器外永远访问不到
EXPOSE 8000

# 健康检查：Docker 靠它判断容器是否真的可用
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')" || exit 1

# ENTRYPOINT 先跑 alembic upgrade head，然后 exec 执行 CMD 的 uvicorn
ENTRYPOINT ["./docker-entrypoint.sh"]

# 生产不加 --reload，并用多个 worker（--workers 只在 Linux 支持，Windows 不支持）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
