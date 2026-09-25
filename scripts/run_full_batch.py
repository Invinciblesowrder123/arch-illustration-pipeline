# -*- coding: utf-8 -*-
"""全量出图批处理：串行跑 output/req_full/ 下的全部需求文件。

串行原因：knowledge/ 是全局文件，每次运行覆盖，不能并发。
用法: .venv/Scripts/python.exe scripts/run_full_batch.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ_DIR = ROOT / "output" / "req_full"
LOG = ROOT / "output" / "req_full" / "batch_log.md"

req_files = sorted(REQ_DIR.glob("*.txt"))
if not req_files:
    raise SystemExit("req_full 目录为空")

lines = [f"# 全量出图批处理 {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
for i, rf in enumerate(req_files, 1):
    requirement = rf.read_text(encoding="utf-8").strip()
    print(f"[{i}/{len(req_files)}] {rf.stem} 开始", flush=True)
    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, str(ROOT / "main.py"),
         "--mode", "local", "--refs", str(ROOT / "references_paper"),
         "--no-search", "--max-attempts", "1", "--text-mode", "in_image",
         "-r", requirement],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    dt = time.time() - t0
    # 从输出里抓 run 目录
    run_dir = ""
    for ln in (proc.stdout or "").splitlines():
        if "生成报告" in ln and "run_" in ln:
            run_dir = ln.split(":")[-1].strip()
            break
    status = "通过" if proc.returncode == 0 else "未通过校验/失败"
    lines.append(f"## {rf.stem}")
    lines.append(f"- 状态: {status}（exit={proc.returncode}，耗时 {dt/60:.1f} 分钟）")
    lines.append(f"- run 目录: {run_dir or '（未见，查 logs/）'}")
    lines.append("")
    with LOG.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[{i}/{len(req_files)}] {rf.stem} 结束: {status}，{dt/60:.1f} 分钟", flush=True)

print("全部完成，汇总见", LOG, flush=True)
