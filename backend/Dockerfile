# ---------- 后端镜像 ----------
# 为什么用 slim 而不是完整版 python 镜像：镜像小（几百 MB vs 1 GB+），
# 拉取快、攻击面小。代价是没有编译工具链，需要编译的包要另外装 build-essential。
FROM python:3.12-slim

# 允许构建时切换 pip 源（国内服务器上装包快很多，不用改代码）
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# PYTHONDONTWRITEBYTECODE：不生成 __pycache__，镜像更小
# PYTHONUNBUFFERED：日志不缓冲，容器里立刻可见（不然 docker logs 会"卡住"）
# PIP_DISABLE_PIP_VERSION_CHECK：安装时不联网检查 pip 版本，避免无谓请求
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先只拷贝依赖清单再安装：只要 requirements.txt 没变，
# 这一层就会被 Docker 缓存命中，改代码后重建镜像只需几秒
COPY requirements.txt .
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt

COPY . .
# Windows 上创建的文件没有可执行权限，必须在镜像里显式补上
RUN chmod +x docker-entrypoint.sh

# 容器里必须监听 0.0.0.0，监听 127.0.0.1 的话容器外永远访问不到
EXPOSE 8000

# 健康检查：Docker 靠它判断容器是否真的可用（这条命令已在本机实测过）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')" || exit 1

# ENTRYPOINT 先跑迁移，然后 exec 执行 CMD 的 uvicorn
ENTRYPOINT ["./docker-entrypoint.sh"]

# 生产不加 --reload，并且用多个 worker；开发环境直接用 uvicorn --reload 即可
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
