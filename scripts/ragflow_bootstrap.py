#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""RAGFlow 本地部署初始化脚本：建用户 → 登录 → 生成 API Key → 建 dataset。

用途：RAGFlow 的 API Key 只能在 Web UI 里点出来，本脚本用官方 HTTP API 打通
同一条链路，便于无浏览器环境下初始化与自动化部署。

要点（对应 RAGFlow v0.27.2 源码）：
- 注册/登录接口要求密码为 RSA 加密串（`api/utils/crypt.py` 的 `crypt()`），
  本脚本直接调用 **容器内** 的 `crypt()`，因此本机不需要安装任何 RSA 库。
- 会话 token 在响应头 `Authorization` 里（`common/connection_utils.py`）。
- API Key（`ragflow-xxx`）由 `POST /v1/system/tokens` 生成，需会话 token 鉴权。

用法：
  python bootstrap.py --email john@ragflow.local --password 'Ragflow@2026'
  python bootstrap.py --email ... --password ... --dataset 考古文献库
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

import requests

DEFAULT_CONTAINER = "docker-ragflow-cpu-1"


def _post(session: requests.Session, url: str, payload: dict | None = None, token: str | None = None):
    headers = {"Authorization": token} if token else {}
    resp = session.post(url, json=payload or {}, headers=headers, timeout=60)
    try:
        body = resp.json()
    except ValueError:
        body = {"code": -1, "message": resp.text[:300]}
    return resp, body


def encrypt_password(container: str, password: str) -> str:
    """调用容器内的 crypt() 加密密码（避免本机安装 pycryptodome）。"""
    cmd = [
        "docker", "exec", "-e", f"RF_PW={password}", container,
        "sh", "-c",
        'cd /ragflow && python -c "'
        "import os;from api.utils.crypt import crypt;print(crypt(os.environ['RF_PW']))\"",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "").strip()
    if not out:
        raise SystemExit(f"密码加密失败:\n{proc.stdout}\n{proc.stderr}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="RAGFlow 初始化：建号 / 取 API Key / 建库")
    ap.add_argument("--base", default="http://localhost", help="RAGFlow 地址（默认 http://localhost）")
    ap.add_argument("--container", default=DEFAULT_CONTAINER, help="RAGFlow 容器名（用于加密密码）")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--nickname", default=None, help="默认取邮箱 @ 前部分")
    ap.add_argument("--token-name", default="workbuddy", help="新建 API Key 的名称")
    ap.add_argument("--dataset", default=None, help="顺带创建的 dataset 名称")
    ap.add_argument("--chunk-method", default="paper", help="dataset 切片方法（默认 paper）")
    args = ap.parse_args()

    nickname = args.nickname or args.email.split("@")[0]
    s = requests.Session()

    # 1) 注册（已存在则忽略，继续登录）
    pw_enc = encrypt_password(args.container, args.password)
    resp, body = _post(s, f"{args.base}/api/v1/users",
                       {"nickname": nickname, "email": args.email, "password": pw_enc})
    if body.get("code") == 0:
        print(f"✓ 注册成功: {args.email}")
    else:
        msg = body.get("message", "")
        if "already registered" in msg:
            print(f"· 用户已存在，跳过注册: {args.email}")
        else:
            print(f"✗ 注册失败: {msg}")
            return 2

    # 2) 登录 → 会话 token 在响应头
    resp, body = _post(s, f"{args.base}/api/v1/auth/login",
                       {"email": args.email, "password": pw_enc})
    session_token = resp.headers.get("Authorization")
    if body.get("code") != 0 or not session_token:
        print(f"✗ 登录失败: {body.get('message')} (HTTP {resp.status_code})")
        return 3
    print(f"✓ 登录成功，会话 token 已获取")

    # 3) 生成 API Key
    resp, body = _post(s, f"{args.base}/api/v1/system/tokens", token=session_token)
    api_key = (body.get("data") or {}).get("token")
    if body.get("code") != 0 or not api_key:
        print(f"✗ 生成 API Key 失败: {body.get('message')}")
        return 4
    print(f"✓ API Key: {api_key}")

    # 4) 用 API Key 打通 /api/v1（SDK 接口，正式调用的鉴权方式）
    r = requests.get(f"{args.base}/api/v1/datasets",
                     headers={"Authorization": f"Bearer {api_key}"}, timeout=60)
    try:
        ds_body = r.json()
    except ValueError:
        ds_body = {}
    if ds_body.get("code") != 0:
        print(f"✗ API Key 鉴权校验失败: {ds_body.get('message')}")
        return 5
    existing = ds_body.get("data") or []
    print(f"✓ API Key 鉴权通过（现有 dataset {len(existing)} 个）")

    # 5) 可选：创建 dataset
    if args.dataset and not any(d.get("name") == args.dataset for d in existing):
        r = requests.post(f"{args.base}/api/v1/datasets",
                          headers={"Authorization": f"Bearer {api_key}"},
                          json={"name": args.dataset, "chunk_method": args.chunk_method,
                                "description": "考古学文献库（RAGFlow 知识层）"},
                          timeout=60)
        created = r.json().get("data") or {}
        if r.json().get("code") != 0:
            print(f"✗ 创建 dataset 失败: {r.json().get('message')}")
            return 6
        print(f"✓ 已创建 dataset「{args.dataset}」id={created.get('id')}")
        dataset_id = created.get("id")
    elif args.dataset:
        dataset_id = next(d["id"] for d in existing if d.get("name") == args.dataset)
        print(f"· dataset「{args.dataset}」已存在 id={dataset_id}")
    else:
        dataset_id = None

    print("\n--- 供 .env 使用 ---")
    print(f"RAGFLOW_BASE_URL={args.base}")
    print(f"RAGFLOW_API_KEY={api_key}")
    if dataset_id:
        print(f"RAGFLOW_DATASET_ID={dataset_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
