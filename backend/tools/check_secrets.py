#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""扫描密钥泄露（工作区 / 暂存区 / 完整 git 历史）。

**为什么需要这个脚本**：这个仓库是**公开**的，一旦把 .env 里的 key 提交上去，
几分钟内就会被爬虫抓走 —— 而删掉文件也救不回来（历史里还在，别人可能已经 fork）。

用法：
    python backend/tools/check_secrets.py            # 扫已跟踪的文件（默认）
    python backend/tools/check_secrets.py --staged   # 只扫暂存区（pre-commit 钩子用）
    python backend/tools/check_secrets.py --history  # 扫所有提交（慢，定期跑）

退出码：0 = 干净，1 = 发现疑似密钥。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

#: 各种厂商的密钥形态。保守一点：宁可误报，也不要漏报。
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # OpenAI / DeepSeek / 硅基流动 / 阿里云百炼 等：sk-xxx
    ("sk- 开头", re.compile(r"sk-[A-Za-z0-9._-]{16,}")),
    # 智谱：32 位 hex + "." + 32 位
    ("智谱格式", re.compile(r"[0-9a-f]{32}\.[0-9a-zA-Z]{32}")),
    # 通用赋值形态：XXX_API_KEY=真值 / "token": "真值"
    (
        "API_KEY 赋值",
        re.compile(r"(?i)(?:api[_-]?key|access[_-]?key|secret|token)\s*[=:]\s*[\"']?([A-Za-z0-9._-]{24,})"),
    ),
    ("Google API Key", re.compile(r"AIza[0-9A-Za-z_-]{30,}")),
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("私钥文件内容", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]

#: 出现这些词就认为是假值/占位符，不算泄露
PLACEHOLDER_HINTS = ("test", "fake", "dummy", "your", "example", "placeholder", "xxxx", "<", "更换")

#: 这些路径不必扫（要么是密钥该待的地方，要么不是源码）
SKIP_PARTS = {".git", ".venv", "venv", "node_modules", "dist", "__pycache__", "media", ".pytest_cache"}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mp3", ".wav", ".zip", ".gz",
    ".db", ".sqlite", ".ico", ".woff", ".woff2", ".ttf", ".pdf",
}
#: 允许放密钥的文件（本来就要有 key）；.env.example 只允许占位符
ENV_FILES = {".env", ".env.local", ".env.production"}


def _is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(hint in lowered for hint in PLACEHOLDER_HINTS)


def _mask(value: str) -> str:
    return f"{value[:6]}…{value[-3:]}" if len(value) > 12 else "***"


def _scan_text(name: str, text: str) -> list[str]:
    findings = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for label, pattern in PATTERNS:
            for match in pattern.finditer(line):
                value = match.group(match.lastindex or 0)
                if _is_placeholder(value):
                    continue
                findings.append(f"{name}:{line_no}  [{label}]  {_mask(value)}")
    return findings


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.stdout


def scan_tracked() -> list[str]:
    findings = []
    for name in _git("ls-files").splitlines():
        if not name or Path(name).name in ENV_FILES:
            continue
        path = Path(name)
        if any(part in SKIP_PARTS for part in path.parts) or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            findings += _scan_text(name, path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return findings


def scan_staged() -> list[str]:
    """只看暂存区 —— pre-commit 钩子用这个，因为它代表"即将进入仓库的内容"。"""
    findings = []
    for name in _git("diff", "--cached", "--name-only", "--diff-filter=ACM").splitlines():
        if not name or Path(name).name in ENV_FILES:
            continue
        path = Path(name)
        if any(part in SKIP_PARTS for part in path.parts) or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        # 读**暂存区版本**（不是工作区版本）：两者可能不一样，用户可以先改再提交
        content = _git("show", f":{name}")
        findings += _scan_text(name, content)
    return findings


def scan_history() -> list[str]:
    findings = []
    commits = _git("rev-list", "--all").split()
    for commit in commits:
        for label, pattern in PATTERNS:
            out = _git("grep", "-n", "-E", pattern.pattern, commit)
            for line in out.splitlines():
                # 格式：<commit>:<path>:<line>:<内容>
                parts = line.split(":", 3)
                if len(parts) < 4:
                    continue
                path, line_no, content = parts[1], parts[2], parts[3]
                if Path(path).name in ENV_FILES:
                    continue
                for match in pattern.finditer(content):
                    value = match.group(match.lastindex or 0)
                    if _is_placeholder(value):
                        continue
                    findings.append(f"{commit[:8]}:{path}:{line_no}  [{label}]  {_mask(value)}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描 API Key 等密钥泄露")
    parser.add_argument("--staged", action="store_true", help="只扫暂存区（pre-commit 用）")
    parser.add_argument("--history", action="store_true", help="扫所有提交历史")
    args = parser.parse_args()

    if args.staged:
        scope, findings = "暂存区", scan_staged()
    elif args.history:
        scope, findings = "完整历史", scan_history()
    else:
        scope, findings = "已跟踪文件", scan_tracked()

    if findings:
        print(f"[HIT] {scope}发现 {len(findings)} 处疑似密钥：")
        for item in findings:
            print(f"  {item}")
        print()
        print("处理建议：")
        print("  1. 如果这是真 key：立刻去厂商控制台**轮换**（作废旧的），再改 .env")
        print("  2. 如果这是测试假值：让值里明显含有 test / fake / example，脚本就会放过")
        print("  3. 真 key 属于 .env（已在 .gitignore 里），不要写在代码或文档里")
        return 1

    print(f"[OK] {scope}没有发现密钥")
    return 0


if __name__ == "__main__":
    sys.exit(main())
