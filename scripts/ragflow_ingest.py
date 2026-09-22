#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""RAGFlow 批量入库脚本（可中断续跑）。

用法：
  # 试切小批（manifest 里的 trial_batch），跑完等解析
  python scripts/ragflow_ingest.py --group trial --wait

  # 全量：论文类进 paper 库、专著类进 book 库（按 chunk_method 自动选目标库）
  python scripts/ragflow_ingest.py --group all --wait

  # 只看进度、不上传（补跑解析或查看存量）
  python scripts/ragflow_ingest.py --group all --status-only

设计要点（多小时的活儿必须能续跑）：
- 以「dataset 内已存在的文件名」为准去重，重复执行只会补传缺失的；
- 解析前先把 UNSTART / FAIL 的文档挑出来批量触发，不重复提交已在跑的；
- 全程打印进度与 ETA（按页数估算），后台跑也能看日志。

清单文件由离线体检生成，见 knowledge/ingest_manifest.json：
  papers（≤150 页，paper 切片）/ books（>150 页，book 切片）/ broken（弃用）。
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
API = f"{BASE}/api/v1"
REFS = Path(os.environ.get("REFS_DIR", PROJECT_ROOT / "references"))
MANIFEST = Path(os.environ.get("MANIFEST", PROJECT_ROOT / "knowledge" / "ingest_manifest.json"))


def _h() -> dict:
    if not KEY:
        raise SystemExit("缺少 RAGFLOW_API_KEY（请检查项目根目录 .env）")
    return {"Authorization": f"Bearer {KEY}"}


def list_datasets() -> list[dict]:
    r = requests.get(f"{API}/datasets", headers=_h(), timeout=60).json()
    return r.get("data") or []


def pick_dataset(datasets: list[dict], chunk_method: str) -> dict:
    for d in datasets:
        if d.get("chunk_method") == chunk_method:
            return d
    raise SystemExit(f"未找到 chunk_method={chunk_method} 的 dataset，请先创建")


def list_docs(dataset_id: str) -> list[dict]:
    out, page = [], 1
    while True:
        r = requests.get(f"{API}/datasets/{dataset_id}/documents", headers=_h(),
                         params={"page": page, "page_size": 100}, timeout=120).json()
        data = r.get("data") or {}
        docs = data.get("docs") or []
        out.extend(docs)
        if len(out) >= (data.get("total") or 0) or not docs:
            return out
        page += 1


def upload(dataset_id: str, files: list[Path], batch_size: int) -> list[str]:
    uploaded = []
    for i in range(0, len(files), batch_size):
        chunk = files[i:i + batch_size]
        opened = []
        try:
            parts = []
            for p in chunk:
                fh = open(p, "rb")
                opened.append(fh)
                parts.append(("file", (p.name, fh)))
            resp = requests.post(f"{API}/datasets/{dataset_id}/documents",
                                 headers=_h(), files=parts, timeout=3600)
            body = resp.json()
        finally:
            for fh in opened:
                fh.close()
        if body.get("code") != 0:
            print(f"  ✗ 批次 {i//batch_size + 1} 上传失败: {body.get('message')}", flush=True)
            continue
        ids = [d.get("id") for d in (body.get("data") or [])]
        uploaded.extend(ids)
        print(f"  已上传 {len(uploaded)}/{len(files)}（本批 {len(ids)} 个）", flush=True)
    return uploaded


def trigger_parse(dataset_id: str, doc_ids: list[str], batch: int = 20) -> None:
    for i in range(0, len(doc_ids), batch):
        r = requests.post(f"{API}/datasets/{dataset_id}/documents/parse", headers=_h(),
                          json={"document_ids": doc_ids[i:i + batch]}, timeout=300).json()
        if r.get("code") != 0:
            print(f"  ✗ 触发解析失败: {r.get('message')}", flush=True)


def progress_snapshot(dataset_id: str) -> tuple[dict, list[dict]]:
    docs = list_docs(dataset_id)
    counts = {"UNSTART": 0, "RUNNING": 0, "DONE": 0, "FAIL": 0, "CANCEL": 0}
    for d in docs:
        counts[d.get("run") or "UNSTART"] = counts.get(d.get("run") or "UNSTART", 0) + 1
    return counts, docs


def wait_done(dataset_id: str, page_map: dict, interval: int, timeout: int) -> None:
    """轮询直到全部解析结束。ETA 按页数估算（文档数会被大部头带偏）。"""
    t0 = time.time()
    while True:
        counts, docs = progress_snapshot(dataset_id)
        done_pages = sum(page_map.get(d["name"], 0) for d in docs if d.get("run") == "DONE")
        total_pages = sum(page_map.get(d["name"], 0) for d in docs)
        elapsed = time.time() - t0
        eta = ""
        if done_pages:
            pp = elapsed / done_pages
            eta = f"　{pp:.2f} 秒/页　预计剩余 {(total_pages - done_pages) * pp / 60:.0f} 分钟"
        print(f"[{elapsed/60:5.1f}min] 完成 {counts.get('DONE',0)} 篇/{done_pages} 页"
              f" · 运行中 {counts.get('RUNNING',0)} · 待解析 {counts.get('UNSTART',0)}"
              f" · 失败 {counts.get('FAIL',0)}{eta}", flush=True)
        if counts.get("RUNNING", 0) == 0 and counts.get("UNSTART", 0) == 0:
            fails = [d for d in docs if d.get("run") == "FAIL"]
            if fails:
                print(f"\n失败 {len(fails)} 篇（原因见 progress_msg）:", flush=True)
                for d in fails[:10]:
                    msg = (d.get("progress_msg") or "").strip().splitlines()
                    print(f"  - {d['name']}: {msg[-1] if msg else '(无)'}", flush=True)
            return
        if time.time() - t0 > timeout:
            print("等待超时，可用 --status-only 继续查看", flush=True)
            return
        time.sleep(interval)


def main() -> int:
    ap = argparse.ArgumentParser(description="RAGFlow 批量入库（可续跑）")
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    ap.add_argument("--group", choices=["trial", "papers", "books", "all"], default="trial")
    ap.add_argument("--batch-size", type=int, default=10)
    ap.add_argument("--wait", action="store_true", help="上传并触发解析后等待完成")
    ap.add_argument("--status-only", action="store_true", help="只打印进度，不上传不触发")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--timeout", type=int, default=6 * 3600)
    args = ap.parse_args()

    man = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.group == "trial":
        groups = [("paper", man.get("trial_papers") or []),
                  ("book", man.get("trial_books") or [])]
    elif args.group == "papers":
        groups = [("paper", man.get("papers") or [])]
    elif args.group == "books":
        groups = [("book", man.get("books") or [])]
    else:
        groups = [("paper", man.get("papers") or []), ("book", man.get("books") or [])]

    datasets = list_datasets()
    for chunk_method, names in groups:
        if not names:
            continue
        ds = pick_dataset(datasets, chunk_method)
        print(f"\n=== {ds['name']}（{chunk_method} 切片，本次 {len(names)} 篇）===", flush=True)
        existing = {d.get("name") for d in list_docs(ds["id"])}
        pending = [REFS / n for n in names if n not in existing and (REFS / n).exists()]

        if args.status_only:
            counts, _ = progress_snapshot(ds["id"])
            print(f"  库内已有 {len(existing)} 篇，未上传 {len(pending)} 篇；状态 {counts}", flush=True)
            continue

        if pending:
            print(f"  待上传 {len(pending)} 篇（库内已有 {len(existing)} 篇，自动跳过）", flush=True)
            upload(ds["id"], pending, args.batch_size)
        else:
            print("  无新增文件，进入解析阶段", flush=True)

        docs = list_docs(ds["id"])
        todo = [d["id"] for d in docs if d.get("run") in (None, "UNSTART", "FAIL")]
        if todo:
            print(f"  触发解析 {len(todo)} 篇…", flush=True)
            trigger_parse(ds["id"], todo)
        if args.wait:
            wait_done(ds["id"], man.get("page_map") or {}, args.interval, args.timeout)
        else:
            counts, _ = progress_snapshot(ds["id"])
            print(f"  已提交，当前状态 {counts}（用 --status-only 查看进度）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
