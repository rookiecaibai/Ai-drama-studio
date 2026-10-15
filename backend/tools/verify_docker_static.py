# -*- coding: utf-8 -*-
"""Docker/Nginx 配置的静态验证（不依赖 Docker 就能查的部分）

诚实说明：**静态验证不能替代实机运行**。它能查的是"路径对不对、配置结构对不对、
必需的文件在不在、忽略清单会不会误伤"，查不了"镜像能不能构建成功、容器能不能起来"。
后者需要 Docker Desktop。
"""
import re
import sys
from pathlib import Path, PurePosixPath

import yaml

ROOT = Path(".").resolve()
ok_count = 0
fail_count = 0


def check(label: str, passed: bool, detail: str = "") -> None:
    global ok_count, fail_count
    if passed:
        ok_count += 1
        print(f"  [OK]   {label}")
    else:
        fail_count += 1
        print(f"  [FAIL] {label}")
    if detail:
        print(f"         {detail}")


print("=" * 70)
print("1. docker-compose.yml 能被 YAML 解析")
print("=" * 70)
compose_path = ROOT / "backend" / "docker-compose.yml"
compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
check("YAML 语法合法", isinstance(compose, dict))
services = compose.get("services", {})
check("三个服务都在（db/api/nginx）", set(services) == {"db", "api", "nginx"}, f"实际: {sorted(services)}")

print()
print("=" * 70)
print("2. 构建上下文与 Dockerfile 路径")
print("=" * 70)
build = services["api"]["build"]
check(
    "context 指向仓库根（镜像里要打前端产物）",
    build["context"] == "..",
    f"实际: {build['context']}",
)
dockerfile = ROOT / build["dockerfile"]
check("dockerfile 路径存在", dockerfile.is_file(), str(dockerfile))

# 构建上下文（仓库根）下的 COPY 源文件必须存在
text = dockerfile.read_text(encoding="utf-8")
copies = re.findall(r"^COPY\s+(?!--from)(.+?)\s+\S+\s*$", text, re.MULTILINE)
missing = []
for group in copies:
    for src in group.split():
        if src.startswith("--") or src == ".":
            src = "." if src == "." else src
        target = (ROOT / src).resolve()
        if src != "." and not target.exists():
            missing.append(src)
check("所有 COPY 的源路径都存在", not missing, f"缺失: {missing}" if missing else f"检查了 {len(copies)} 条 COPY")
check(
    "COPY 有 --from（多阶段构建：前端产物从构建阶段拷过来）",
    "--from=frontend" in text,
)
check(
    "WORKDIR 是 /srv/app/backend（和仓库布局一致）",
    "/srv/app/backend" in text,
    "前端路径用 parents[2] 计算，布局必须一致",
)

print()
print("=" * 70)
print("3. 前端产物路径计算（这是容器里界面 404 的根因）")
print("=" * 70)
# app/main.py 的算法：Path(__file__).resolve().parents[2] / "frontend" / "dist"
local = Path("C:/repo/ai-drama-studio/backend/app/main.py")
check(
    "本地开发布局：<repo>/backend/app/main.py -> <repo>/frontend/dist",
    local.parents[2] / "frontend" / "dist" == Path("C:/repo/ai-drama-studio/frontend/dist"),
)
new_container = Path("/srv/app/backend/app/main.py")
check(
    "新容器布局：/srv/app/backend/app/main.py -> /srv/app/frontend/dist",
    new_container.parents[2] / "frontend" / "dist" == Path("/srv/app/frontend/dist"),
)
old_container = Path("/app/app/main.py")
old_result = old_container.parents[2] / "frontend" / "dist"
check(
    "旧布局 /app 会算成 /frontend/dist（已修复的 bug，这里复现一下）",
    old_result == Path("/frontend/dist"),
    f"旧布局结果: {old_result} —— 界面永远找不到，API 却正常，属于最难查的'一半能用'",
)

print()
print("=" * 70)
print("4. .dockerignore 会不会误伤（密钥是否被排除、必需文件是否被留下）")
print("=" * 70)
di = ROOT / ".dockerignore"
check(".dockerignore 存在且位于仓库根", di.is_file(), "上下文是仓库根，忽略文件也必须在这里")
raw = di.read_text(encoding="utf-8")
patterns = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def matches(path: str, pattern: str) -> bool:
    """简化版匹配：够用来验证"某文件会不会被忽略"这个是非问题。"""
    if pattern.endswith("/"):
        base = pattern.rstrip("/")
        return path == base or path.startswith(base + "/")
    if pattern.startswith("**/"):
        tail = pattern[3:]
        return PurePosixPath(path).match(tail) or PurePosixPath(path).name == tail
    return PurePosixPath(path).match(pattern)


def is_ignored(path: str) -> bool:
    result = False
    for pat in patterns:
        negate = pat.startswith("!")
        if negate:
            pat = pat[1:]
        if matches(path, pat):
            result = not negate
    return result


must_be_ignored = ["backend/.env", ".venv/lib/python3.12/site-packages/x.py", "backend/media/shots/1.jpg"]
for path in must_be_ignored:
    check(f"会被忽略（重要）: {path}", is_ignored(path))

must_survive = [
    "backend/requirements.txt",
    "backend/app/main.py",
    "backend/app/static/swagger-ui/swagger-ui-bundle.js",
    "backend/alembic.ini",
    "backend/docker-entrypoint.sh",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/src/main.tsx",
]
for path in must_survive:
    check(f"不会被忽略（必需）: {path}", not is_ignored(path))

print()
def strip_comments(text: str, marker: str = "#") -> str:
    """剥掉注释行再做结构分析。

    为什么必须剥：第一版校验脚本直接在原文上跑正则，结果注释里出现的
    "没有 `location /`" 和 "apt-get install ffmpeg" 都被当成了真实配置，
    报了两个**假 FAIL**。教训和前面测试图那次一样 —— 校验工具本身也会错，
    报 FAIL 时先怀疑工具，再怀疑被检查的对象。
    """
    return "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith(marker))


print("=" * 70)
print("5. nginx.conf 结构检查")
print("=" * 70)
conf_raw = (ROOT / "backend" / "nginx" / "nginx.conf").read_text(encoding="utf-8")
conf = strip_comments(conf_raw)
check("花括号配平", conf.count("{") == conf.count("}"), f"{{={conf.count('{')} }}={conf.count('}')}")
check("定义了 upstream", "upstream ai_drama_api" in conf)
check("有 location /（否则 Web 界面打不开）", re.search(r"^\s*location\s+/\s*\{", conf, re.MULTILINE) is not None)
check("upstream 名字和 proxy_pass 一致", conf.count("proxy_pass http://ai_drama_api") >= 5)

locations = re.findall(r"^\s*location\s+([^\s{]+)[^{]*\{", conf, re.MULTILINE)
check("location 数量合理", len(locations) >= 5, f"找到: {locations}")
# 每个 location 块里都要有 proxy_pass
blocks = re.split(r"^\s*location\s+[^\s{]+[^{]*\{", conf, flags=re.MULTILINE)[1:]
for name, block in zip(locations, blocks):
    body = block.split("\n    }")[0]
    check(f"location {name} 里有 proxy_pass", "proxy_pass" in body)
check("媒体路径关了缓冲（大视频边收边发）", "proxy_buffering off" in conf)
check("没有把 mp4 加进 gzip（压缩已压缩格式是白烧 CPU）", "video/" not in conf.split("gzip_types")[1].split(";")[0])

print()
print("=" * 70)
print("6. 容器里有没有 ffmpeg（视频合成依赖）")
print("=" * 70)
reqs = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8").lower()
dockerfile_code = strip_comments(text)
check("requirements.txt 含 imageio-ffmpeg（自带含 libx264 的静态 ffmpeg）", "imageio-ffmpeg" in reqs)
check("requirements.txt 含 pillow（本地运镜/占位图备用）", "pillow" in reqs)
check(
    "Dockerfile 没有真的 apt 装 ffmpeg（用的是内置那份，省 300MB）",
    not re.search(r"^\s*RUN\s+.*apt-get\s+install", dockerfile_code, re.MULTILINE),
    "注释里提到 apt-get 不算（这就是第一版的假 FAIL）",
)

print()
print("=" * 70)
print("7. compose 的关键配置（媒体持久化 + 新功能环境变量）")
print("=" * 70)
api = services["api"]
volumes = api.get("volumes", [])
check(
    "media 目录挂了卷（否则容器重建后生成的图和视频全丢）",
    any("media" in str(v) for v in volumes),
    f"实际: {volumes}",
)
env = api.get("environment", {})
required_env = [
    "IMAGE_PROVIDER", "IMAGE_API_KEY", "IMAGE_MODEL",
    "TTS_PROVIDER", "TTS_API_KEY", "TTS_MODEL", "TTS_VOICE",
    "VIDEO_CLIP_PROVIDER", "VIDEO_MODEL", "VIDEO_SIZE", "VIDEO_SEND_DURATION",
    "DAILY_CLIP_LIMIT", "DAILY_IMAGE_LIMIT", "DAILY_AUDIO_LIMIT",
    "MEDIA_ROOT", "JWT_SECRET_KEY", "DATABASE_URL",
]
absent = [k for k in required_env if k not in env]
check("四种模态 + 限额外加媒体根目录都注入了", not absent, f"缺: {absent}" if absent else f"共 {len(env)} 个环境变量")
check("密钥用 ${VAR:-默认} 从 .env 注入，没写死在 compose 里", "${JWT_SECRET_KEY" in str(env.get("JWT_SECRET_KEY", "")))
check("api 用 expose 而不是 ports（不直接暴露公网）", "expose" in api and "ports" not in api)
check("api 等 db 健康后才启动", api["depends_on"]["db"]["condition"] == "service_healthy")
check("nginx 等 api 健康后才启动", services["nginx"]["depends_on"]["api"]["condition"] == "service_healthy")
check("数据库用了 utf8mb4（存 emoji 不会报错）", any("utf8mb4" in str(c) for c in services["db"]["command"]))

print()
print("=" * 70)
print(f"结果：{ok_count} 项通过，{fail_count} 项失败")
print("=" * 70)
if fail_count:
    print("\n需要注意：上面有 FAIL 项，实机之前先修掉。")
else:
    print("\n静态检查全通过。但**镜像能否构建、容器能否起来、界面能否打开，必须实机验证** ——")
    print("这些静态检查查不到（比如 npm ci 是否成功、alembic 在 MySQL 上是否通过）。")
    print("步骤见 docs/05-部署验证清单.md")
sys.exit(1 if fail_count else 0)
