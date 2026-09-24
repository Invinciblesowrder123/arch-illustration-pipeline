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
import glob
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
    r = _safe_json(requests.get(f"{API}/datasets", headers=_h(), timeout=60))
    return (r or {}).get("data") or []


def pick_dataset(datasets: list[dict], chunk_method: str) -> dict:
    for d in datasets:
        if d.get("chunk_method") == chunk_method:
            return d
    raise SystemExit(f"未找到 chunk_method={chunk_method} 的 dataset，请先创建")


def list_docs(dataset_id: str) -> list[dict]:
    out, page = [], 1
    while True:
        r = _safe_json(requests.get(f"{API}/datasets/{dataset_id}/documents", headers=_h(),
                                    params={"page": page, "page_size": 100}, timeout=120))
        data = (r or {}).get("data") or {}
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


def _safe_json(r) -> dict | None:
    """把响应就地拦成"可解析的 JSON 或 None"。

    为什么必须有：容器刚重启（或上游未就绪）时，接口会返回 HTML 错误页、502 或空响应。
    直接 `.json()` 会抛 JSONDecodeError 让**无人值守的入库脚本整个崩掉**——
    2026-09-24 实测：停摆自愈重启容器后立刻触发解析，撞上非 JSON 响应，脚本退出。
    """
    if r is None or r.status_code != 200:
        return None
    text = (r.text or "").strip()
    if not text.startswith("{"):
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _wait_api_ready(timeout: int = 300, interval: int = 5) -> bool:
    """等 `/api/v1` 真正可用——Web 首页（静态资源）会先于 API 就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{API}/datasets", headers=_h(),
                             params={"page": 1, "page_size": 1}, timeout=10)
            if _safe_json(r) is not None:
                return True
        except requests.RequestException:
            pass
        time.sleep(interval)
    return False


def trigger_parse(dataset_id: str, doc_ids: list[str], batch: int = 10, attempts: int = 3) -> int:
    """触发解析。小批量 + 失败重试（文档多时接口会偶发 Internal server error）。

    返回成功触发的文档数。**任何单次失败都不许让脚本崩掉**——最多打印并跳过。
    """
    ok = 0
    for i in range(0, len(doc_ids), batch):
        chunk = doc_ids[i:i + batch]
        for k in range(attempts):
            body, why = None, ""
            try:
                r = requests.post(f"{API}/datasets/{dataset_id}/documents/parse", headers=_h(),
                                  json={"document_ids": chunk}, timeout=300)
                body = _safe_json(r)
                if body is None:
                    why = f"HTTP {r.status_code} 返回非 JSON（服务可能尚未就绪），" \
                          f"前 80 字: {(r.text or '')[:80]!r}"
            except requests.RequestException as e:
                why = f"请求异常 {type(e).__name__}: {str(e)[:120]}"
            if body is not None and body.get("code") == 0:
                ok += len(chunk)
                break
            if k == attempts - 1:
                msg = (body or {}).get("message") or why
                print(f"  ✗ 触发解析失败（已重试 {attempts} 次）: {msg}", flush=True)
            else:
                time.sleep(5 * (k + 1))
    return ok


def progress_snapshot(dataset_id: str) -> tuple[dict, list[dict]]:
    docs = list_docs(dataset_id)
    counts = {"UNSTART": 0, "RUNNING": 0, "DONE": 0, "FAIL": 0, "CANCEL": 0}
    for d in docs:
        counts[d.get("run") or "UNSTART"] = counts.get(d.get("run") or "UNSTART", 0) + 1
    return counts, docs


def _executor_log_mtime(pattern: str) -> float | None:
    """取 task_executor 日志的最新写入时间（存活探针）。

    判"停摆"不能只看「有没有文档完成」——大部头（几百页扫描件）解析二三十分钟不完成
    是正常的，只看完成数会误判并反复重启容器。执行器只要在干活就会持续写日志，
    故用日志 mtime 作为存活信号。返回 None 表示拿不到日志（调用方应降级判断）。
    """
    if not pattern:
        return None
    best: float | None = None
    for p in glob.glob(pattern):
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if best is None or m > best:
            best = m
    return best


def _restart_ragflow_container() -> bool:
    """重启 RAGFlow 容器让卡死的 task_executor 复活（仅在 --auto-restart 时调用）。"""
    name = os.environ.get("RAGFLOW_CONTAINER", "docker-ragflow-cpu-1")
    print(f"  ⇄ 重启容器 {name} 以复活卡死的 task_executor…", flush=True)
    r = subprocess.run(["docker", "restart", name], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print(f"  ✗ 重启失败: {(r.stderr or '').strip()[:200]}", flush=True)
        return False
    # 先等 Web 首页（静态资源先行）
    for _ in range(30):
        try:
            if requests.get(f"{BASE}/", timeout=5).status_code in (200, 302, 308):
                break
        except requests.RequestException:
            pass
        time.sleep(5)
    # 再等 API 真正就绪：只等首页就触发解析，会撞上非 JSON 响应（2026-09-24 实测踩到）
    print("  ⏳ 等待 /api/v1 就绪…", flush=True)
    if not _wait_api_ready():
        print("  ✗ API 300s 内未就绪，后续触发可能失败（可稍后手动再跑一次本命令）", flush=True)
        return False
    print("  ✓ API 已就绪", flush=True)
    return True


def wait_done(dataset_id: str, page_map: dict, interval: int, timeout: int,
              wave_mode: bool = False, auto_restart: bool = False,
              stall_minutes: int = 15, executor_log: str = "",
              allowed: set | None = None) -> None:
    """轮询到没有文档在跑为止，并检测"执行器停摆"。

    退出条件分两种（用 --wave 区分）：
    - 整库模式（默认）：RUNNING==0 **且** UNSTART==0 才算跑完——否则整库排队时
      一旦出现瞬间空档就会误判为结束（2026-09-22 实测踩到：论文组还剩 119 篇未触发
      就被判结束，脚本跳去跑专著组）。
    - 分波模式（--wave N）：只看 RUNNING==0，因为剩余未触发的文档本来就该留到下一波。
    两种情况都要求**连续两次采样**满足条件，避免波次间空档误判。

    停摆检测（2026-09-23 血的教训）：TEI 读超时多次之后，task_executor 会**静默停摆**
    ——进程还在、CPU 归零、日志不再写，但文档状态仍是 RUNNING 且进度停在 0%。
    此时队列不会自己恢复，必须重启容器。故：
      · 连续 stall_minutes 分钟「无任何文档完成」且仍有 RUNNING → 判定停摆，先补触发，
        再等一轮；仍无进展且开了 --auto-restart → 重启容器。

    损坏文件识别（2026-09-24 教训）：**补触发全部被服务端拒收**（code=102
    "Internal server error"，根因多为 PDF 损坏缺 /Root）时，重启容器救不回来——
    无人值守跑了一夜、每 20 分钟重启一次、一晚 12 次全是空转。此时把这些文档
    标记为 unfixable 并跳过：脚本继续等其余文档，坏文件由人工修复后重传补齐。
    """
    t0 = time.time()
    idle_hits = 0
    stall_polls = 0
    last_done = None
    last_progress_at = time.time()
    restarts = 0
    unfixable: set[str] = set()
    _, docs0 = progress_snapshot(dataset_id)
    if allowed is not None:
        docs0 = [d for d in docs0 if d.get("name") in allowed]
    base_done = sum(1 for d in docs0 if d.get("run") == "DONE")
    base_pages = sum(page_map.get(d["name"], 0) for d in docs0 if d.get("run") == "DONE")
    while True:
        counts, docs = progress_snapshot(dataset_id)
        if allowed is not None:
            docs = [d for d in docs if d.get("name") in allowed]
            counts = {}
            for d in docs:
                k = d.get("run") or "UNSTART"
                counts[k] = counts.get(k, 0) + 1
        done_pages = sum(page_map.get(d["name"], 0) for d in docs if d.get("run") == "DONE")
        total_pages = sum(page_map.get(d["name"], 0) for d in docs)
        elapsed = time.time() - t0
        # 疑似损坏（服务端拒收）的文档不计入"运行中"——否则脚本会为它们等到超时
        eff_running = [d for d in docs if d.get("run") == "RUNNING" and d["id"] not in unfixable]
        # 速率按「本次运行期间的增量」计算——否则会把历史耗时算进本次，ETA 严重偏小
        new_docs = counts.get("DONE", 0) - base_done
        new_pages = done_pages - base_pages
        eta = ""
        if new_docs > 0 and elapsed > 30:
            remain = len(eff_running) + counts.get("UNSTART", 0)
            rate_pages = new_pages / elapsed
            eta = (f"　本次 {new_pages} 页/{(elapsed/60):.0f}min = {rate_pages*60:.1f} 页/分钟"
                   f"　预计剩余 {(total_pages - done_pages) / rate_pages / 60:.0f} 分钟"
                   if rate_pages > 0 else f"　剩余 {remain} 篇")
        skip_note = f" · 跳过疑似损坏 {len(unfixable)}" if unfixable else ""
        print(f"[{elapsed/60:5.1f}min] 完成 {counts.get('DONE',0)} 篇/{done_pages} 页"
              f" · 运行中 {len(eff_running)} · 未触发 {counts.get('UNSTART',0)}"
              f" · 失败 {counts.get('FAIL',0)}{skip_note}{eta}", flush=True)

        # ---- 停摆检测：先看执行器日志是否还在写（存活探针），再决定是否干预 ----
        if last_done is None or counts.get("DONE", 0) > last_done:
            last_done = counts.get("DONE", 0)
            last_progress_at = time.time()
        stalled_min = (time.time() - last_progress_at) / 60
        if stalled_min >= stall_minutes and len(eff_running) > 0:
            log_m = _executor_log_mtime(executor_log)
            if log_m is not None and (time.time() - log_m) < stall_minutes * 60:
                # 执行器还在写日志 → 在干活，只是大文档没跑完，别干预
                print(f"  · {stalled_min:.0f} 分钟无完成，但执行器日志仍在更新"
                      f"（{int((time.time()-log_m)/60)} 分钟前），判定为大文档解析中，继续等待", flush=True)
                last_progress_at = time.time()  # 重置窗口，避免刷屏
            else:
                running = [d["id"] for d in eff_running]
                why = "执行器日志已停写" if log_m is not None else "拿不到执行器日志"
                print(f"  ⚠ 已连续 {stalled_min:.0f} 分钟零完成，有 {len(running)} 篇卡在 RUNNING，"
                      f"{why}（疑似 task_executor 停摆）", flush=True)
                ok = trigger_parse(dataset_id, running[:30], batch=5)
                if ok == 0:
                    # 服务端拒收全部补触发 → 文档多半损坏（如 PDF 缺 /Root）。
                    # 重启容器救不回来，只会死循环空转（2026-09-24 实测：一夜重启 12 次）。
                    print(f"  ✗ 补触发 0/{len(running)} 成功——服务端拒收，疑似文件损坏。"
                          f"已跳过这 {len(running)} 篇（不再重启容器空转）；"
                          f"修复文件后重传并重跑本命令即可补齐", flush=True)
                    unfixable.update(running)
                elif restarts > 0 and auto_restart:
                    print("  ⇄ 补触发后仍零完成，重启容器（--auto-restart）", flush=True)
                    if _restart_ragflow_container():
                        print("  ✓ 容器已重启，重新触发卡住的文档", flush=True)
                        trigger_parse(dataset_id, running[:30], batch=5)
                    else:
                        print("  ✗ 重启失败，保留补触发结果", flush=True)
                restarts += 1
                last_progress_at = time.time()  # 给一轮观察窗口

        idle = not eff_running and (wave_mode or counts.get("UNSTART", 0) == 0)
        if idle:
            idle_hits += 1
            if idle_hits >= 2:  # 连续两次为空，确认不是波次间的空档
                if counts.get("UNSTART", 0):
                    print(f"\n本波结束：仍有 {counts['UNSTART']} 篇未触发解析，"
                          f"再次运行本命令（可加 --wave N）继续。", flush=True)
                fails = [d for d in docs if d.get("run") == "FAIL"]
                if fails:
                    print(f"\n失败 {len(fails)} 篇（原因见 progress_msg）:", flush=True)
                    for d in fails[:10]:
                        msg = (d.get("progress_msg") or "").strip().splitlines()
                        print(f"  - {d['name']}: {msg[-1] if msg else '(无)'}", flush=True)
                return
        else:
            idle_hits = 0
            # 自愈：长时间有 UNSTART 且没有在跑的任务时，补触发（触发接口会偶发 500）
            unstart = [d["id"] for d in docs if (d.get("run") or "UNSTART") == "UNSTART"]
            if unstart and counts.get("RUNNING", 0) == 0:
                stall_polls += 1
                if stall_polls >= 3:
                    print(f"  ↻ 补触发 {len(unstart)} 篇未排队文档（接口偶发 500，自动重试）", flush=True)
                    trigger_parse(dataset_id, unstart[:30], batch=5)
                    stall_polls = 0
            elif counts.get("RUNNING", 0) > 0:
                stall_polls = 0

        if time.time() - t0 > timeout:
            print("等待超时，可用 --status-only 继续查看", flush=True)
            return
        time.sleep(interval)


def main() -> int:
    ap = argparse.ArgumentParser(description="RAGFlow 批量入库（可续跑）")
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    ap.add_argument("--group", choices=["trial", "papers", "books", "books_b", "all"], default="trial",
                    help="trial=试切批次；papers=论文组；books=全部专著；books_b=专著择优清单（manifest 的 books_b，14 本/5108 页）；all=全部")
    ap.add_argument("--batch-size", type=int, default=10)
    ap.add_argument("--wave", type=int, default=0,
                    help="本次最多触发解析 N 篇（0=不限）。用于分波提交，避免一次性打爆嵌入服务")
    ap.add_argument("--retry-fail", action="store_true",
                    help="只重试 FAIL 文档，不动 UNSTART（用于失败补救，避免重复提交已排队的文档）")
    ap.add_argument("--include-running", action="store_true",
                    help="把停在 RUNNING 的文档也重新触发（执行器停摆或容器重启后需要）")
    ap.add_argument("--auto-restart", action="store_true",
                    help="检测到执行器停摆且补触发无效时，自动重启 RAGFlow 容器（无人值守推荐）")
    ap.add_argument("--stall-minutes", type=int, default=15,
                    help="连续多少分钟零完成即判定停摆（默认 15）")
    ap.add_argument("--ragflow-container", default=os.environ.get("RAGFLOW_CONTAINER", "docker-ragflow-cpu-1"),
                    help="RAGFlow 容器名（--auto-restart 用）")
    ap.add_argument("--executor-log",
                    default=os.environ.get("RAGFLOW_EXECUTOR_LOG",
                                           r"D:\AI\RAGFlow\ragflow\docker\ragflow-logs\task_executor_*.log"),
                    help="task_executor 日志通配路径（存活探针：日志还在写就说明在执行，不误判停摆）")
    ap.add_argument("--upload-only", action="store_true", help="只上传，不触发解析")
    ap.add_argument("--wait", action="store_true", help="上传并触发解析后等待完成")
    ap.add_argument("--status-only", action="store_true", help="只打印进度，不上传不触发")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--timeout", type=int, default=6 * 3600)
    args = ap.parse_args()

    man = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.ragflow_container:
        os.environ["RAGFLOW_CONTAINER"] = args.ragflow_container
    if args.group == "trial":
        groups = [("paper", man.get("trial_papers") or []),
                  ("book", man.get("trial_books") or [])]
    elif args.group == "papers":
        groups = [("paper", man.get("papers") or [])]
    elif args.group == "books":
        groups = [("book", man.get("books") or [])]
    elif args.group == "books_b":
        groups = [("book", man.get("books_b") or [])]
    else:
        groups = [("paper", man.get("papers") or []), ("book", man.get("books") or [])]

    datasets = list_datasets()
    for chunk_method, names in groups:
        if not names:
            continue
        ds = pick_dataset(datasets, chunk_method)
        allowed = set(names)
        print(f"\n=== {ds['name']}（{chunk_method} 切片，本次 {len(names)} 篇）===", flush=True)
        all_docs = list_docs(ds["id"])
        existing = {d.get("name") for d in all_docs}
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

        # 只对本组清单内的文件动手（books_b 择优清单能排除暂不跑的专著）
        docs = [d for d in all_docs if d.get("name") in allowed]
        if args.retry_fail:
            todo = [d["id"] for d in docs if d.get("run") == "FAIL"]
            print(f"  仅重试失败文档：{len(todo)} 篇", flush=True)
        elif args.include_running:
            todo = [d["id"] for d in docs if (d.get("run") or "UNSTART") != "DONE"]
            print(f"  重新触发全部未完成文档（含 RUNNING）：{len(todo)} 篇", flush=True)
        else:
            todo = [d["id"] for d in docs if d.get("run") in (None, "UNSTART", "FAIL")]
        if args.wave:
            if len(todo) > args.wave:
                print(f"  分波提交：本次触发 {args.wave} 篇，剩余 {len(todo) - args.wave} 篇待下次"
                      f"（避免一次性打爆嵌入服务）", flush=True)
            todo = todo[:args.wave]
        if args.upload_only:
            print("  --upload-only：已上传但不触发解析", flush=True)
        elif todo:
            print(f"  触发解析 {len(todo)} 篇…", flush=True)
            trigger_parse(ds["id"], todo)
        if args.wait:
            wait_done(ds["id"], man.get("page_map") or {}, args.interval, args.timeout,
                      wave_mode=bool(args.wave), auto_restart=args.auto_restart,
                      stall_minutes=args.stall_minutes, executor_log=args.executor_log,
                      allowed=allowed)
        else:
            counts, _ = progress_snapshot(ds["id"])
            print(f"  已提交，当前状态 {counts}（用 --status-only 查看进度）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
