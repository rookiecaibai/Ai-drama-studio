# -*- coding: utf-8 -*-
"""在容器内部直接访问后端 8000 端口，把响应头打出来

用途：和"从宿主机经 nginx 8080 访问"做逐项对比，
看清 nginx 到底在中间加了什么（或去掉了什么）。
"""
import json
import urllib.request

TARGETS = [
    "http://127.0.0.1:8000/api/v1/health",
    "http://127.0.0.1:8000/assets/index-Y8PRxLq0.js",
    "http://127.0.0.1:8000/",
]

INTERESTING = ["content-type", "content-length", "content-encoding", "cache-control",
               "server", "etag", "last-modified", "vary", "accept-ranges", "date"]

for url in TARGETS:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            print(f"\n--- 直连后端 {url} ---")
            print(f"  HTTP {resp.status}")
            headers = {k.lower(): v for k, v in resp.headers.items()}
            for key in INTERESTING:
                if key in headers:
                    print(f"  {key}: {headers[key]}")
            body = resp.read()
            print(f"  实际收到 {len(body)} 字节")
    except Exception as exc:  # noqa: BLE001
        print(f"\n--- 直连后端 {url} ---")
        print(f"  失败: {type(exc).__name__}: {exc}")

# 顺便说明：容器里 "localhost" 是容器自己，api 是服务名
print("\n容器内还能用服务名访问：")
for host in ["api", "127.0.0.1"]:
    try:
        with urllib.request.urlopen(f"http://{host}:8000/api/v1/health", timeout=5) as resp:
            print(f"  http://{host}:8000/api/v1/health -> HTTP {resp.status} {json.loads(resp.read())}")
    except Exception as exc:  # noqa: BLE001
        print(f"  http://{host}:8000/... -> 失败: {type(exc).__name__}")
