# 在 PyCharm 里跑这个项目

> 目标：让你能在 PyCharm 里 **一键启动、打断点、单条跑测试**，而不是每次切到命令行敲命令。
> 这份文档按"从零打开项目"的顺序写，每一步都讲了**为什么**。

---

## 0. 前提：虚拟环境已经建好了

我替你把最麻烦的一步做完了：

```
ai-drama-studio\.venv\            <- 项目专属虚拟环境（Python 3.12）
```

**为什么必须有虚拟环境**：你机器上有 3.10 / 3.12 / 3.13 三个 Python。
如果所有项目都用全局 Python，A 项目要 `fastapi 0.100`、B 项目要 `fastapi 0.143`，
两边必然打架，而且哪天误删一个包会连带弄坏别的项目。
虚拟环境就是"每个项目一个独立的包仓库"，互不影响。

`.venv` 已经写进 `.gitignore`，**不会被提交到 Git** —— 这是正确的：
别人 clone 你的代码后自己 `pip install -r requirements.txt` 重建即可，
把虚拟环境提交上去只会让仓库变成几个 GB。

### 建这个环境时真实踩到的三个坑（比教程本身值钱）

**坑一：pip 直接崩了**

```
UnicodeDecodeError: 'gbk' codec can't decode byte 0x80 in position 34
```

我最初把 `requirements.txt` 的注释写成中文。pip 读取 requirement 文件时用的是
**系统区域编码**（你这里是 cp936/GBK），而文件是 UTF-8，于是解析器还没开始干活就炸了。
现在文件第一行就写着 `keep this file pure ASCII`。

**坑二：pip 卡死 + 零输出，真正的元凶是损坏的缓存**

现象极具迷惑性：`pip install six` 只要 2 秒，`urllib` 访问镜像 0.37 秒，
网络明明通，但一装这个项目就挂住，而且**一行输出都不打印**。
我一开始归因到 cffi 编译、pip 版本、网络……全都错了。

最后靠"控制变量 + 看进程 CPU"定位到真凶：**pip 的 HTTP 缓存被损坏了**。
缓存目录里有 2565 个文件、其中两个分别高达 **3.1 GB 和 2.4 GB**——
这是之前反复强杀 pip 进程留下的残缺下载。pip 读缓存时死循环，
所以 CPU 飙到几百秒却没有任何输出。

验证：加上 `--no-cache-dir`（跳过缓存），**完全相同的命令 21.7 秒装完**。

```powershell
# 根治：清掉损坏缓存（一次性，能释放好几个 GB）
python -m pip cache purge
```

**坑三：`--only-binary=:all:` 是防"源码编译"，不是治卡死的**

requirements 里的 `--only-binary=:all:` 是另一回事：它强制只用预编译 wheel，
避免 pip 去编译 cffi 这类带 C 代码的包（没装 C 工具链的机器会卡很久或报错）。
它有价值，但**不是坑二的解药**，别记混。

**这三件事值得记住**：
1. `gbk` / `cp936` 报错不是乱码问题，是**跨环境编码假设**问题；
2. "网络慢"经常是错误直觉，**证据在进程 CPU 时间和缓存文件里，不在感觉里**；
3. 诊断卡死要控制变量：同一条命令，只加/删一个参数，看结果变不变。

---

## 1. 打开项目：要选 `ai-drama-studio`，不要选 `backend`

File → Open → 选到

```
C:\Users\bmw12\Documents\deepseek-harness\default-workspace\ai-drama-studio
```

**为什么选根目录**：Git 仓库根、`.gitignore`、`README.md`、`docs/`、`.github/` 都在这一层。
如果你直接打开 `backend`，PyCharm 会把 `backend` 当成项目根，
于是 `.gitignore` 失效（`ai_drama.db`、`.env`、`__pycache__` 全都会冒出来让你提交）、
`.github/workflows/ci.yml` 也看不到了。

---

## 2. 指定解释器：切成刚建好的 `.venv`

打开后右下角会显示当前解释器（可能是 `Python 3.13`），**点它**：

```
右下角解释器名  →  Add New Interpreter  →  Add Local Interpreter...
    →  左侧选 "Existing"（有的版本叫 Select existing）
    →  路径选：
       C:\Users\bmw12\Documents\deepseek-harness\default-workspace\ai-drama-studio\.venv\Scripts\python.exe
```

或者走菜单：`File → Settings → Project: ai-drama-studio → Python Interpreter → Add Local Interpreter`。

**切换成功后怎么确认**：在 PyCharm 里打开 `backend/requirements.txt`，
如果每条依赖左边没有黄色波浪线（"Package not installed"），就说明装好了。

**为什么不用系统 Python**：见第 0 节。另外 PyCharm 里解释器选错是最常见的
"我明明装了包，为什么还报 ModuleNotFoundError" 的原因 —— 你装在了 A 环境，PyCharm 用的是 B 环境。

---

## 3. 把 `backend` 标记为源码根目录（关键一步，否则满屏红）

在左侧项目树里右键 `backend` 文件夹：

```
Mark Directory as  →  Sources Root
```

标记成功后 `backend` 图标会变成蓝色。

**为什么必须做**：你的代码里写的是 `from app.core.config import get_settings`，
但 `app` 这个包实际位于 `backend/app`。只有让 PyCharm 知道 `backend` 是"源码根"，
它才能把 `app` 解析出来。不做这一步，`import` 全是红色波浪线，
自动补全、跳转定义、重构全部失效，写代码体验会差到无法忍受。

> 顺带说：这也是为什么 `pytest.ini` 里有一行 `pythonpath = .` ——
> 它让 **pytest 运行时**也能找到 `app`。
> PyCharm 的编辑器解析（IDE 层面）和 pytest 的运行时导入（解释器层面）
> 是两套机制，**两边都要配**，只配一边就会出现"能跑但满屏红"或"不红但跑不起来"。

---

## 4. 设置测试框架为 pytest

`File → Settings → Tools → Python Integrated Tools`，
把 `Default test runner` 改成 **pytest**。

**为什么**：PyCharm 默认可能按 `unittest` 规则去发现测试，
而我们的测试是纯函数式 pytest 风格（没有继承 `TestCase` 类）。
不改成 pytest，左边那些绿色小三角可能根本不出现。

---

## 5. 启动服务：右键 `run.py` → Run / Debug

我在 [backend/run.py](../backend/run.py) 专门留了一个启动入口，
就是为了让 PyCharm 能"一键运行 + 断点调试"。

- **正常启动**：右键 `run.py` → `Run 'run'`
- **调试启动**：右键 `run.py` → `Debug 'run'` ← 打断点用这个

启动后控制台会打印接口文档地址，浏览器打开 <http://127.0.0.1:8000/docs>。

### ⚠️ 一个必须知道的细节：工作目录

打开 `Run → Edit Configurations`，检查 `Working directory` 是不是：

```
$PROJECT_DIR$\backend
```

**为什么这个特别重要**：项目里有两处"相对路径"依赖工作目录：

| 相对路径 | 写在哪 | 工作目录不对的后果 |
|---|---|---|
| `.env` | `core/config.py` 的 `env_file=".env"` | 读不到配置，你以为改了 `.env` 其实没生效 |
| `./ai_drama.db` | 默认 `DATABASE_URL` | 数据库文件建到别的目录，看到的是"空库" |

这两个坑的共同症状是"**我明明配了，怎么没反应**"，
排查方向第一件事就是确认工作目录。

### 想开热重载（改代码自动重启）

`run.py` 默认关掉了 reload，原因写在文件注释里：
**`reload=True` 时 uvicorn 会另起子进程，PyCharm 的断点会失效**。
需要热重载时，用 PyCharm 底部的 Terminal 标签跑：

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

调试时用 `run.py`（关重载、能打断点），写业务时用终端（开重载、改完即生效）。

---

## 6. 跑测试：三种粒度

| 粒度 | 操作 |
|---|---|
| 全部 | 左侧右键 `backend/tests` 目录 → `Run 'pytest in tests'` |
| 单个文件 | 打开 `test_generation.py`，右键编辑器空白处 → Run |
| 单个用例 | 点函数名左边的绿色三角 → Run / **Debug** |

**为什么测试在 PyCharm 里跑比命令行值钱**：单个用例可以直接 `Debug`。
比如你想知道"模型返回垃圾时到底走到哪一步"，
在 `test_llm_bad_output_returns_502` 里打个断点按 Debug，
能一行行看 `_extract_json` 怎么抛异常、错误处理器怎么把它变成 502。
**看代码一百遍，不如打断点走一遍。**

---

## 7. 用 `.http` 文件调接口（比 Postman 省事）

我写了 [backend/api.http](../backend/api.http)，里面是全部 11 个请求。
在 PyCharm 里打开它，每一行 `###` 分隔的请求左边都有一个**绿色三角**，点一下直接发送。

**为什么推荐这种方式**：这个文件会跟着代码一起提交到 Git。
接口改了字段名，`.http` 一起改，review 时也能看见；
而 Postman 的请求存在本地，团队里谁都不知道你测的是什么。

**执行顺序**：先启动服务，然后按 2 → 3(可选) → 6 → 8 → 9 的顺序点。
（第 8 步会调用 mock 模型生成 6 个镜头，不需要 API Key。）

---

## 8. 推荐打开的 PyCharm 面板

| 面板 | 位置 | 在这里干什么 |
|---|---|---|
| **Terminal** | 底部 | 跑 uvicorn --reload、跑 pip、跑 git |
| **Python Console** | 底部 | 交互式试代码，比如 `from app.core.config import get_settings; get_settings()` |
| **Git** | 底部 | 看改动、提交、对比历史。**别用右键"Commit"糊里糊涂全提交** |
| **Database** | 右侧边栏 → Database | 连你的 MySQL（端口 3306），直接看表和写 SQL |
| **Services** | 底部 | 用来看 Docker 容器（装了 Docker Desktop 之后会出现） |

**Database 面板连本机 MySQL 的方法**（顺便解决我拿不到你 MySQL 密码的问题）：
`+` → Data Source → MySQL → Host `localhost`、Port `3306`、User `root`、
Password 填你的密码 → Test Connection。
连上后你就能直接在 IDE 里跑 SQL 建库，然后告诉我密码，我把项目切到 MySQL 验证一遍。

---

## 9. 常见问题排查表

| 症状 | 原因 | 解决 |
|---|---|---|
| `ModuleNotFoundError: No module named 'app'` | 没标 Sources Root | 回到第 3 步 |
| `ModuleNotFoundError: No module named 'fastapi'` | 解释器选错了（用的是全局 Python） | 回到第 2 步，确认右下角是 `.venv` |
| `import` 是红色波浪线但能跑 | IDE 没标 Sources Root，或没装依赖 | 同上，两边都要配 |
| 改了 `.env` 不生效 | 工作目录不对，或配置是单例缓存 | 检查 Working directory；重启进程（`get_settings` 有 lru_cache） |
| 端口 8000 被占用 | 上一个进程没退干净 | 点 PyCharm 左上角**红色方块 Stop**（关窗口不一定杀进程），或换 8001 |
| 改代码不自动重启 | `run.py` 默认关 reload | 用终端跑 `--reload`，见第 5 节 |
| 测试全是红但命令行能过 | Default test runner 不是 pytest | 回到第 4 步 |
| `pip install` 报 `UnicodeDecodeError: 'gbk' codec` | requirements 文件含非 ASCII 字符 | 见第 0 节坑一 |
| `pip install` 长时间无响应、零输出 | pip 缓存损坏（见第 0 节坑二） | `python -m pip cache purge` 或加 `--no-cache-dir` |

**关于最后一条，这是我在搭项目时真实踩到的坑，值得你记住**：

我一开始把 `requirements.txt` 的注释写成了中文。
在你机器上一装就崩：

```
UnicodeDecodeError: 'gbk' codec can't decode byte 0x80 in position 34
```

原因是 **pip 读取 requirement 文件时用的是系统区域编码**（你这里是 cp936/GBK），
而文件是 UTF-8 编码的中文。**解决办法是让 `requirements.txt` 保持纯 ASCII**，
中文说明挪到文档里 —— 现在文件开头第一行就写着这条规定。

这个坑在 Docker 构建时也会出现（容器里 locale 是 C/POSIX），
所以"配置文件尽量保持 ASCII"是个值得养成的习惯。
**这类问题不是你英语不好造成的，而是"跨环境编码假设"造成的** ——
能讲清这一点，比背十道八股更有说服力。

---

## 10. 一页速查

```
打开项目          File → Open → ai-drama-studio（根目录）
解释器            右下角 → Add Local Interpreter → Existing → .venv\Scripts\python.exe
源码根            右键 backend → Mark Directory as → Sources Root
测试框架          Settings → Tools → Python Integrated Tools → pytest
启动服务          右键 backend/run.py → Debug
调接口            打开 backend/api.http，点绿色三角
跑测试            右键 backend/tests → Run；单条用例可 Debug
工作目录          必须是 $PROJECT_DIR$\backend
```
