#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""RAGFlow 知识层运维脚本：模型体检 / 文档上传 / 触发解析 / 解析状态 / 检索试跑。

配置从 D:\\AI\\Painter\\.env 读取（RAGFLOW_BASE_URL / RAGFLOW_API_KEY / RAGFLOW_DATASET_ID），
避免密钥出现在命令行与 shell 历史里。

用法：
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
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

BASE = os.environ.get("RAGFLOW_BASE_URL", "http://localhost").rstrip("/")
KEY = os.environ.get("RAGFLOW_API_KEY", "").strip()
DEFAULT_DS = os.environ.get("RAGFLOW_DATASET_ID", "").strip()
API = f"{BASE}/api/v1"


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
    """连续轮询解析状态直到全部完成（或超时）。"""
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
            return 0
        time.sleep(args.interval)
    print("等待超时")
    return 1


def cmd_set_defaults(args):
    """设置租户默认模型。内置 TEI 嵌入的模型名直接写 TEI_MODEL（如 BAAI/bge-m3），
    provider 可留空或写 Builtin —— 源码中该情形解析为内置服务，不需要 tenant_model 行。"""
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


def main() -> int:
    ap = argparse.ArgumentParser(description="RAGFlow 运维工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("datasets", help="列出所有 dataset").set_defaults(func=cmd_datasets)
    sub.add_parser("models", help="查看租户默认模型").set_defaults(func=cmd_models)

    sd = sub.add_parser("set-defaults", help="设置租户默认嵌入/对话模型")
    sd.add_argument("--embd", default="BAAI/bge-m3", help="嵌入模型名（内置 TEI 用 TEI_MODEL 的值）")
    sd.add_argument("--llm", default="", help="对话模型名（可留空）")
    sd.set_defaults(func=cmd_set_defaults)

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
    sds.add_argument("--embd", default="", help="嵌入模型名，如 BAAI/bge-m3")
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
