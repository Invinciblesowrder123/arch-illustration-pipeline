#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""RAGFlow 知识层运维脚本：容器启停 + 模型体检 / 文档上传 / 触发解析 / 解析状态 / 检索试跑。

配置从 D:\\AI\\Painter\\.env 读取（RAGFLOW_BASE_URL / RAGFLOW_API_KEY / RAGFLOW_DATASET_ID），
避免密钥出现在命令行与 shell 历史里。

用法：
  # 容器级（docker compose 栈）—— 不用再记 compose 文件的路径
  python scripts/ragflow_ops.py up --wait       # 启动 RAGFlow 并等待 Web 就绪
  python scripts/ragflow_ops.py down            # 停止 RAGFlow（保留容器与数据）
  python scripts/ragflow_ops.py restart --wait  # 重启
  python scripts/ragflow_ops.py ps              # 容器状态
  python scripts/ragflow_ops.py logs ragflow-cpu -f
  python scripts/ragflow_ops.py health          # Web/API 连通性自检
  python scripts/ragflow_ops.py up --with-tei   # 回退：附带本地 TEI 嵌入服务

  # 知识库级（RAGFlow REST API）
  python scripts/ragflow_ops.py embedding-init                 # 配置云 embedding（登记+验证）
  python scripts/ragflow_ops.py models
  python scripts/ragflow_ops.py upload --dataset <id> "D:\\path\\a.pdf" "D:\\path\\b.pdf"
  python scripts/ragflow_ops.py parse --dataset <id>                # 不指定 --docs 则解析该库全部文档
  python scripts/ragflow_ops.py status --dataset <id>
  python scripts/ragflow_ops.py retrieve "二里头 绿松石龙形器 匚形"
  python scripts/ragflow_ops.py datasets
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import embedding_guide  # noqa: E402  服务商推荐表，与 pipeline 预检共用一份

BASE = os.environ.get("RAGFLOW_BASE_URL", "http://localhost").rstrip("/")
KEY = os.environ.get("RAGFLOW_API_KEY", "").strip()
DEFAULT_DS = os.environ.get("RAGFLOW_DATASET_ID", "").strip()
API = f"{BASE}/api/v1"

# RAGFlow 栈（docker compose）位置：compose 文件在 ragflow/docker 子目录，
# 不在 D:\AI\RAGFlow 顶层 —— 在顶层执行会报 "no configuration file provided"。
COMPOSE_FILE = Path(r"D:\AI\RAGFlow\ragflow\docker\docker-compose.yml")
COMPOSE_DIR = COMPOSE_FILE.parent


def _h() -> dict:
    if not KEY:
        raise SystemExit("缺少 RAGFLOW_API_KEY（请检查项目根目录的 .env）")
    return {"Authorization": f"Bearer {KEY}"}


def _call(method: str, path: str, **kw):
    resp = requests.request(method, f"{API}{path}", headers=_h(), timeout=120, **kw)
    try:
        body = resp.json()
    except ValueError:
        raise SystemExit(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return body


def cmd_datasets(_args):
    body = _call("GET", "/datasets")
    for d in body.get("data") or []:
        print(f"{d.get('id')}  {d.get('name')}  文档 {d.get('document_count')}  chunk {d.get('chunk_count')}")
    print(f"合计 {len(body.get('data') or [])} 个 dataset")


def cmd_models(_args):
    """查租户默认模型（嵌入 / 对话 / 重排）。"""
    body = _call("GET", "/users/me/models")
    data = body.get("data") or {}
    for kind, models in (data.items() if isinstance(data, dict) else []):
        if isinstance(models, list):
            names = [m.get("name") or m.get("llm_name") for m in models if isinstance(m, dict)]
            print(f"{kind}: {names}")
        else:
            print(f"{kind}: {models!r}")


def cmd_upload(args):
    files = []
    opened = []
    try:
        for p in args.files:
            fh = open(p, "rb")
            opened.append(fh)
            files.append(("file", (Path(p).name, fh)))
        body = requests.post(f"{API}/datasets/{args.dataset}/documents",
                             headers=_h(), files=files, timeout=1800)
        data = body.json()
    finally:
        for fh in opened:
            fh.close()
    if data.get("code") != 0:
        raise SystemExit(f"上传失败: {data.get('message')}")
    items = data.get("data") or []
    for d in items:
        print(f"✓ 已上传 {d.get('name')}  id={d.get('id')}  size={d.get('size')}")
    print(f"共 {len(items)} 个文件")


def cmd_parse(args):
    doc_ids = args.docs
    if not doc_ids:
        body = _call("GET", f"/datasets/{args.dataset}/documents",
                     params={"page": 1, "page_size": 100})
        data = body.get("data") or {}
        docs = data.get("docs") if isinstance(data, dict) else data
        doc_ids = [d.get("id") for d in (docs or []) if d.get("id")]
        if not doc_ids:
            raise SystemExit("该 dataset 下没有文档")
        print(f"将对 {len(doc_ids)} 个文档触发解析（接口要求显式传 document_ids）")
    body = _call("POST", f"/datasets/{args.dataset}/documents/parse", json={"document_ids": doc_ids})
    if body.get("code") != 0:
        raise SystemExit(f"触发解析失败: {body.get('message')}")
    print("✓ 已触发解析（DeepDoc 后台执行，可用 status / wait 查看进度）")


def cmd_status(args):
    body = _call("GET", f"/datasets/{args.dataset}/documents",
                 params={"page": 1, "page_size": 100})
    docs = (body.get("data") or {})
    docs = docs.get("docs") if isinstance(docs, dict) else docs
    progress = (body.get("data") or {}).get("progress") if isinstance(body.get("data"), dict) else None
    run = (body.get("data") or {}).get("progress_msg") if isinstance(body.get("data"), dict) else None
    for d in docs or []:
        run_status = d.get("run")
        print(f"{d.get('id')}  run={run_status}  chunks={d.get('chunk_count')}  {d.get('name')}")
    if progress is not None:
        print(f"总进度: {progress:.1%}")
    if run and args.verbose:
        print((run or "")[-800:])


def cmd_delete(args):
    """从 dataset 删除文档（同时清理其 chunk）。"""
    body = _call("DELETE", f"/datasets/{args.dataset}/documents", json={"ids": args.docs})
    if body.get("code") != 0:
        raise SystemExit(f"删除失败: {body.get('message')}")
    print(f"✓ 已删除 {len(args.docs)} 个文档")


def cmd_retrieve(args):
    body = _call("POST", "/retrieval", json={
        "question": args.question,
        "dataset_ids": [args.dataset],
        "knn_top_k": args.top_k,
        "page_size": args.top_k,
        "similarity_threshold": args.threshold,
    })
    if body.get("code") != 0:
        raise SystemExit(f"检索失败: {body.get('message')}")
    data = body.get("data") or {}
    chunks = data.get("chunks") or []
    print(f"命中 {len(chunks)} 个片段（total={data.get('total')}）\n")
    for i, c in enumerate(chunks, 1):
        page = ""
        pos = c.get("positions")
        if pos:
            page = f" p.{pos[0][0] + 1}"
        print(f"[{i}] {c.get('document_keyword') or c.get('document_name')}{page}"
              f"  相似度 {c.get('similarity', 0):.4f}")
        print(f"    {str(c.get('content',''))[:120]}…\n")


def cmd_set_dataset(args):
    """更新 dataset 属性（检索时嵌入模型取自 dataset.embd_id，必须显式设置，
    否则报 Provider not found for model .）。"""
    payload = {"dataset_id": args.dataset}
    if args.embd:
        payload["embedding_model"] = args.embd
    if args.name:
        payload["name"] = args.name
    if args.language:
        payload["language"] = args.language
    body = _call("PUT", f"/datasets/{args.dataset}", json=payload)
    if body.get("code") != 0:
        raise SystemExit(f"更新失败: {body.get('message')}")
    d = _call("GET", f"/datasets/{args.dataset}").get("data") or {}
    print(f"✓ 已更新 dataset: name={d.get('name')} embd_id={d.get('embd_id')} "
          f"chunk_method={d.get('chunk_method')} language={d.get('language')}")


def cmd_ask(args):
    """连续轮询解析状态直到全部完成（或超时）。失败文档必须返回非零。"""
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        body = _call("GET", f"/datasets/{args.dataset}/documents",
                     params={"page": 1, "page_size": 100})
        data = body.get("data") or {}
        docs = data.get("docs") if isinstance(data, dict) else data
        done = sum(1 for d in docs or [] if d.get("run") == "DONE")
        running = sum(1 for d in docs or [] if d.get("run") == "RUNNING")
        failed = sum(1 for d in docs or [] if d.get("run") == "FAIL")
        print(f"运行中 {running} / 完成 {done} / 失败 {failed}（共 {len(docs or [])}）")
        if running == 0:
            if failed:
                print(f"✗ 解析结束，但有 {failed} 个文档失败；请用 status --verbose 查看原因。")
                return 2
            return 0
        time.sleep(args.interval)
    print("等待超时")
    return 1


def _embedding_ref_matches(actual: str, expected: str) -> bool:
    """允许 RAGFlow 返回完整引用，目标配置写短名时也能做明确校验。"""
    actual, expected = (actual or "").strip(), (expected or "").strip()
    if not expected:
        return True
    return actual == expected or actual.startswith(expected + "@")


def _embedding_state(tenant_embd: str, providers: list[str]) -> tuple[str, str]:
    """判定租户默认 embedding 的可用状态，返回 (状态, 说明)。

    - ready  ：指向已登记的 provider，可用。
    - local  ：裸模型名或 @Builtin，依赖本地 TEI，API 侧判断不了，只提示不阻断。
    - missing：未设置，或指向的 provider 没登记 —— 必须让用户去添加。
    """
    embd = (tenant_embd or "").strip()
    if not embd:
        return "missing", "租户未设置默认 embedding 模型"
    parts = embd.rsplit("@", 2)
    provider = parts[-1].strip() if len(parts) >= 2 else ""
    if not provider:
        return "local", f"默认 embedding 是裸模型名「{embd}」，未指向 provider（依赖本地 TEI）"
    if provider.lower() == "builtin":
        return "local", f"默认 embedding 指向本地 Builtin（{embd}）"
    if provider not in providers:
        return "missing", (f"默认 embedding 指向 provider「{provider}」，但该 provider 未登记"
                           f"（已登记：{providers or '无'}）")
    return "ready", f"已配置：{embd}"


def cmd_set_defaults(args):
    """设置租户默认模型。

    云 embedding 由 RAGFlow 模型供应商管理；这里仅写入用户明确传入的模型引用，
    不自动创建 provider，也不触发已有文档重解析。
    """
    me = _call("GET", "/users/me")
    me_data = me.get("data") or {}
    tid = me_data.get("id") or me_data.get("user_id")
    if not tid:
        raise SystemExit(f"未能获取用户 id，/users/me 返回: {json.dumps(me, ensure_ascii=False)[:300]}")
    payload = {
        "tenant_id": tid,
        "llm_id": args.llm or "",
        "embd_id": args.embd,
        "asr_id": "",
        "img2txt_id": "",
    }
    body = _call("PATCH", "/users/me/models", json=payload)
    if body.get("code") != 0:
        raise SystemExit(f"设置默认模型失败: {body.get('message')}")
    print(f"✓ 已设置租户默认模型: embd_id={args.embd}" + (f", llm_id={args.llm}" if args.llm else ""))
    after = _call("GET", "/users/me/models").get("data") or {}
    for k in ("embd_id", "llm_id", "rerank_id"):
        print(f"  当前 {k}: {after.get(k)}")
    actual = after.get("embd_id") or after.get("embedding_model") or ""
    if args.embd and not _embedding_ref_matches(str(actual), args.embd):
        raise SystemExit(f"租户 embedding 回读不一致：期望 {args.embd}，实际 {actual}")


def _mask(key: str) -> str:
    key = key or ""
    return key[:6] + "…" + key[-4:] if len(key) > 12 else "***"


def cmd_embedding_init(args):
    """配置云 embedding：注册 provider → 建实例并写入 API key → 连通验证。

    边界：只做登记与验证。不设租户默认模型、不切换任何 dataset、不触发已有文档重解析，
    因此不会让旧向量与新模型混用，也不会悄悄产生批量 embedding 费用。
    """
    api_key = (args.api_key or os.environ.get("ZHIPU_API_KEY", "")).strip()
    if not api_key:
        raise SystemExit("缺少 API key：用 --api-key 传入，或设置环境变量 ZHIPU_API_KEY")
    provider, model, instance = args.provider, args.model, args.instance
    print(f"provider={provider}  model={model}  instance={instance}  key={_mask(api_key)}")

    body = requests.put(f"{API}/providers", headers=_h(),
                        json={"provider_name": provider}, timeout=60).json()
    if body.get("code") != 0:
        # 重复执行不算失败：provider 已登记就复用，继续写实例与验证。
        if "already exists" not in str(body.get("message") or "").lower():
            raise SystemExit(f"注册 provider 失败: {body.get('message')}")
        print(f"· provider 已存在，复用: {provider}")
    else:
        print(f"✓ provider 已注册: {provider}")

    body = requests.post(f"{API}/providers/{provider}/instances", headers=_h(), json={
        "instance_name": instance,
        "api_key": api_key,
        "base_url": args.base_url,
        "region": "default",
        "model_info": [{"model_type": ["embedding"], "model_name": model,
                        "max_tokens": args.max_tokens}],
    }, timeout=120).json()
    if body.get("code") != 0:
        msg = str(body.get("message") or "")
        # 同名实例已存在时，用 PUT 覆盖其 api_key 等配置，保证可重复执行。
        if "exist" not in msg.lower():
            raise SystemExit(f"创建实例失败: {msg}")
        body = requests.put(
            f"{API}/providers/{provider}/instances/{instance}", headers=_h(), json={
                "instance_name": instance,
                "api_key": api_key,
                "base_url": args.base_url,
                "region": "default",
                "model_info": [{"model_type": ["embedding"], "model_name": model,
                                "max_tokens": args.max_tokens}],
            }, timeout=120).json()
        if body.get("code") != 0:
            raise SystemExit(f"更新实例失败: {body.get('message')}")
        print(f"· 实例已存在，已更新配置: {instance}")
    else:
        print(f"✓ 实例已创建: {instance}")

    body = requests.post(f"{API}/providers/{provider}/connection", headers=_h(), json={
        "api_key": api_key, "base_url": args.base_url, "region": "default",
    }, timeout=180).json()
    if body.get("code") != 0:
        raise SystemExit(f"连通验证失败: {body.get('message')}")
    print("✓ 连通验证通过（云端 embedding 可用）")
    print(f"\n建库时使用这个引用：{model}@{instance}@{provider}")
    print("后续若要全量迁移，必须先建试验库回归；旧 bge-m3 向量不可与新模型混用。")


# ---------------------------------------------------------------------------
# 容器级运维：管理 docker compose 栈（up / down / restart / ps / logs / health）
# ---------------------------------------------------------------------------

def _compose(args_list, with_tei: bool = False):
    """在 compose 文件所在目录执行 docker compose，返回退出码。

    cwd 设为 COMPOSE_DIR 是为了让 compose 正确加载同目录的 .env 与相对路径。
    with_tei=True 时把 tei-cpu 追加进 COMPOSE_PROFILES，用于回退到本地嵌入。
    """
    if not COMPOSE_FILE.exists():
        raise SystemExit(f"找不到 compose 文件: {COMPOSE_FILE}")
    env = None
    if with_tei:
        env = dict(os.environ)
        cur = env.get("COMPOSE_PROFILES", "")
        if "tei-cpu" not in cur:
            env["COMPOSE_PROFILES"] = f"{cur},tei-cpu" if cur else "tei-cpu"
        print("· 本次启动包含本地 TEI（tei-cpu）")
    try:
        proc = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), *args_list],
            cwd=str(COMPOSE_DIR), env=env,
        )
    except FileNotFoundError:
        raise SystemExit("未找到 docker 命令：请先启动 Docker Desktop 并确认 docker 在 PATH 中。")
    return proc.returncode


def _ragflow_alive(timeout=3.0):
    """探测 RAGFlow Web(80) 是否已就绪。"""
    try:
        r = requests.get(BASE, timeout=timeout)
        return r.status_code < 500
    except requests.RequestException:
        return False


def _wait_ready(timeout):
    deadline = time.time() + timeout
    print(f"等待 RAGFlow 就绪（最长 {timeout}s）…")
    while time.time() < deadline:
        if _ragflow_alive():
            print(f"✓ RAGFlow 已就绪: {BASE}")
            return
        time.sleep(3)
    print(f"⚠ 超时仍未就绪: {BASE}（容器可能仍在初始化，可稍后用 health 复查）")


def cmd_up(args):
    rc = _compose(["up", "-d"], with_tei=getattr(args, "with_tei", False))
    if rc != 0:
        raise SystemExit(rc)
    print("\n✓ 已发起启动（docker compose up -d）")
    if args.wait:
        _wait_ready(args.timeout)


def cmd_down(_args):
    rc = _compose(["down"])
    if rc != 0:
        raise SystemExit(rc)
    print("\n✓ 已停止 RAGFlow 栈（容器与数据保留）")


def cmd_restart(args):
    _compose(["down"])
    rc = _compose(["up", "-d"], with_tei=getattr(args, "with_tei", False))
    if rc != 0:
        raise SystemExit(rc)
    print("\n✓ 已重启 RAGFlow 栈")
    if args.wait:
        _wait_ready(args.timeout)


def cmd_ps(_args):
    _compose(["ps"])


def cmd_logs(args):
    tail = ["logs", "--tail", str(args.tail)]
    if args.follow:
        tail.append("-f")
    if args.service:
        tail.append(args.service)
    _compose(tail)


def cmd_health(args):
    """分层检查 Web、API、dataset 与可选 embedding 目标引用。"""
    if not _ragflow_alive():
        print(f"✗ Web 不可达: {BASE}（Docker/RAGFlow 可能未启动，试 `up --wait`）")
        return 1
    print(f"✓ Web 可达: {BASE}")
    try:
        datasets = _call("GET", "/datasets").get("data") or []
        print(f"✓ API 正常，现有 dataset {len(datasets)} 个")
        tenant_models = _call("GET", "/users/me/models").get("data") or {}
        tenant_embd = tenant_models.get("embd_id") or tenant_models.get("embedding_model") or ""
        print(f"  租户默认 embedding: {tenant_embd or '未设置'}")

        # ---- embedding 可用性：缺模型是最常见的"检索全挂"根因，必须显式提示 ----
        providers = [str(p.get("name")) for p in (_call("GET", "/providers").get("data") or [])
                     if isinstance(p, dict) and p.get("name")]
        print(f"  已登记 provider: {providers or '无'}")
        state, reason = _embedding_state(str(tenant_embd), providers)
        if state == "ready":
            print(f"✓ embedding 可用: {tenant_embd}")
        elif state == "local":
            print(f"⚠ {reason} —— 若本机未启用 TEI，检索会报 Provider not found；"
                  f"需要本地嵌入时用 `up --with-tei`")
        else:
            print(embedding_guide.render_setup_guide(reason))
            return 1
        if args.embedding_ref:
            print(f"· 迁移目标 embedding: {args.embedding_ref}（仅校验配置，不会切换）")
        mismatches = []
        if args.embedding_ref and not _embedding_ref_matches(str(tenant_embd), args.embedding_ref):
            mismatches.append(f"租户默认: {tenant_embd or '未设置'}")
        for d in datasets:
            embd = d.get("embd_id") or d.get("embedding_model") or ""
            if args.embedding_ref and not _embedding_ref_matches(str(embd), args.embedding_ref):
                mismatches.append(f"{d.get('name') or d.get('id')}: {embd or '未设置'}")
        if mismatches:
            print("⚠ 现有 dataset 尚未使用迁移目标（这是预期的，切换前需建试验库并重解析）：")
            for item in mismatches:
                print(f"  - {item}")
        return 0
    except SystemExit as exc:
        print(f"✗ API 不可用或鉴权失败: {exc}")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="RAGFlow 运维工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # ---- 容器级运维（docker compose 栈）----
    up = sub.add_parser("up", help="启动 RAGFlow 栈（docker compose up -d）")
    up.add_argument("--wait", action="store_true", help="启动后等待 Web 就绪")
    up.add_argument("--timeout", type=int, default=180, help="等待就绪的最长秒数")
    up.add_argument("--with-tei", action="store_true",
                    help="临时附带本地 TEI 嵌入服务（默认走 .env，当前已关闭）")
    up.set_defaults(func=cmd_up)

    sub.add_parser("down", help="停止 RAGFlow 栈（保留容器与数据）").set_defaults(func=cmd_down)

    rs = sub.add_parser("restart", help="重启 RAGFlow 栈")
    rs.add_argument("--wait", action="store_true", help="重启后等待 Web 就绪")
    rs.add_argument("--timeout", type=int, default=180)
    rs.add_argument("--with-tei", action="store_true", help="临时附带本地 TEI 嵌入服务")
    rs.set_defaults(func=cmd_restart)

    sub.add_parser("ps", help="查看 RAGFlow 容器状态").set_defaults(func=cmd_ps)

    lg = sub.add_parser("logs", help="查看 RAGFlow 容器日志")
    lg.add_argument("service", nargs="?", default=None, help="服务名，如 ragflow-cpu / mysql / es01")
    lg.add_argument("--tail", type=int, default=100)
    lg.add_argument("-f", "--follow", action="store_true", help="持续跟踪")
    lg.set_defaults(func=cmd_logs)

    hp = sub.add_parser("health", help="分层检查 RAGFlow Web/API/dataset")
    hp.add_argument("--embedding-ref", default=os.environ.get("RAGFLOW_EMBEDDING_REF", "").strip(),
                    help="只校验迁移目标，不会切换 dataset")
    hp.set_defaults(func=cmd_health)

    # ---- 知识库级（REST API）----
    sub.add_parser("datasets", help="列出所有 dataset").set_defaults(func=cmd_datasets)
    sub.add_parser("models", help="查看租户默认模型").set_defaults(func=cmd_models)

    sd = sub.add_parser("set-defaults", help="设置租户默认嵌入/对话模型")
    sd.add_argument("--embd", default=os.environ.get("RAGFLOW_EMBEDDING_REF", "BAAI/bge-m3").strip(),
                    help="embedding 模型引用；云 provider 需使用 RAGFlow 的 model@instance@provider 格式")
    sd.add_argument("--llm", default="", help="对话模型名（可留空）")
    sd.set_defaults(func=cmd_set_defaults)

    ei = sub.add_parser("embedding-init", help="配置云 embedding provider（登记+验证，不切库）")
    ei.add_argument("--provider", default="ZHIPU-AI")
    ei.add_argument("--model", default="embedding-3")
    ei.add_argument("--instance", default="zhipu-main")
    ei.add_argument("--base-url", default="https://open.bigmodel.cn/api/paas/v4")
    ei.add_argument("--api-key", default="", help="不传则读环境变量 ZHIPU_API_KEY")
    ei.add_argument("--max-tokens", type=int, default=8192)
    ei.set_defaults(func=cmd_embedding_init)

    up = sub.add_parser("upload", help="上传文档")
    up.add_argument("--dataset", default=DEFAULT_DS)
    up.add_argument("files", nargs="+")
    up.set_defaults(func=cmd_upload)

    pa = sub.add_parser("parse", help="触发解析")
    pa.add_argument("--dataset", default=DEFAULT_DS)
    pa.add_argument("--docs", nargs="*")
    pa.set_defaults(func=cmd_parse)

    de = sub.add_parser("delete", help="删除文档")
    de.add_argument("--dataset", default=DEFAULT_DS)
    de.add_argument("docs", nargs="+")
    de.set_defaults(func=cmd_delete)

    st = sub.add_parser("status", help="查看解析状态")
    st.add_argument("--dataset", default=DEFAULT_DS)
    st.add_argument("--verbose", action="store_true")
    st.set_defaults(func=cmd_status)

    rt = sub.add_parser("retrieve", help="检索试跑")
    rt.add_argument("question")
    rt.add_argument("--dataset", default=DEFAULT_DS)
    rt.add_argument("--top-k", type=int, default=5)
    rt.add_argument("--threshold", type=float, default=0.2)
    rt.set_defaults(func=cmd_retrieve)

    sds = sub.add_parser("set-dataset", help="更新 dataset 属性（嵌入模型 / 名称 / 语言）")
    sds.add_argument("--dataset", default=DEFAULT_DS)
    sds.add_argument("--embd", default="", help="embedding 模型引用，如 BAAI/bge-m3@Builtin 或云 provider 完整引用")
    sds.add_argument("--name", default="", help="dataset 名称（建议一并带上，避免被清空）")
    sds.add_argument("--language", default="", help="文档语言，如 Chinese")
    sds.set_defaults(func=cmd_set_dataset)

    wt = sub.add_parser("wait", help="轮询等待解析完成")
    wt.add_argument("--dataset", default=DEFAULT_DS)
    wt.add_argument("--interval", type=int, default=15)
    wt.add_argument("--timeout", type=int, default=1800)
    wt.set_defaults(func=cmd_ask)

    args = ap.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
